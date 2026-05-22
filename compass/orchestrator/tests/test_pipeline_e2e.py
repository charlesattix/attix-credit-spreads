"""End-to-end integration test for compass.orchestrator.pipeline.

Uses injected fakes for signal_generator / gate / sizer / router so the
test runs without CC1–CC4 modules landed and without any broker SDK.

The integration covers all three public entry points: run() in dry-run
+ paper, cleanup(), reconcile(). Each test also verifies the JSONL
audit files land in the right place under audit_dir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest

from compass.orchestrator import pipeline
from compass.orchestrator.pipeline import cleanup, reconcile, run
from compass.orchestrator.types import (
    GatedSignal,
    PipelineResult,
    SignalIntent,
    SizedOrder,
)


# ───────────────────────────────────────────────────────────────────────────
# Fakes injected through the run(...) hooks
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class _FakeOrder:
    stream: str
    client_order_id: str
    status: str = "FILLED"
    broker_order_id: str = "BR-1"
    legs: List[Dict] = field(default_factory=list)
    contract_count: int = 1


def _fake_gate(intents, portfolio, date):
    """Allow OPEN, block everything else."""
    return [
        GatedSignal(
            intent=i,
            gate_status=("ALLOW" if i.action == "OPEN" else "BLOCK"),
            gate_reasons=[] if i.action == "OPEN" else [f"action={i.action}"],
            confidence_adj=1.0,
        )
        for i in intents
    ]


def _fake_sizer(eligible, portfolio, connector):
    """Produce one SizedOrder per ALLOW/DEGRADE input."""
    return [
        SizedOrder(
            gated=g,
            contract_count=1,
            risk_allocation=500.0,
            short_strike=420.0,
            long_strike=415.0,
            expected_credit=0.55,
            max_loss_dollars=445.0,
            expiration="2026-06-19",
            port_weight_consumed=0.025,
            sizing_reasons=["risk=$500"],
        )
        for g in eligible
    ]


def _fake_router(sized, connector, submit: bool = True):
    """Return one FILLED FakeOrder per sized input when submit=True,
    PENDING otherwise (mirrors dry-run vs paper semantics)."""
    status = "FILLED" if submit else "PENDING"
    return [
        _FakeOrder(
            stream=s.gated.intent.stream,
            client_order_id=f"{s.gated.intent.date}-{s.gated.intent.stream}",
            status=status,
            legs=[
                {"symbol": f"FAKE-SHORT-{s.gated.intent.stream}", "side": "SELL",
                 "quantity": 1},
                {"symbol": f"FAKE-LONG-{s.gated.intent.stream}", "side": "BUY",
                 "quantity": 1},
            ],
            contract_count=s.contract_count,
        )
        for s in sized
    ]


def _fake_signal_generator(intent_dicts):
    """Closure that returns the pre-built dicts regardless of date."""
    def _gen(date):
        return intent_dicts
    return _gen


# ───────────────────────────────────────────────────────────────────────────
# Tests — full pipeline
# ───────────────────────────────────────────────────────────────────────────

class TestRunHappyPath:
    def test_dry_run_full_pipeline(self, sample_intent_dicts, fake_connector,
                                       audit_dir):
        result = run(
            date="2026-05-22",
            mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )

        assert isinstance(result, PipelineResult)
        assert result.mode == "dry-run"
        assert result.date == "2026-05-22"
        assert result.status == "OK"
        assert result.n_intents == 8                       # 8 raw signals
        assert result.n_allow == 6                         # 6 OPENs in fixture
        assert result.n_block == 2                         # HOLD + NONE
        assert result.n_sized == 6
        # dry-run never calls the broker -> n_submitted via PENDING bucket
        assert result.n_submitted == 6
        assert result.n_filled == 0
        assert result.gross_risk_dollars == pytest.approx(6 * 500.0)
        assert result.portfolio_equity == pytest.approx(100_000.0)

    def test_paper_mode_marks_orders_filled(self, sample_intent_dicts,
                                                  fake_connector, audit_dir):
        result = run(
            date="2026-05-22",
            mode="paper",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        assert result.status == "OK"
        assert result.n_filled == 6
        assert result.n_rejected == 0

    def test_per_stream_counts_populated(self, sample_intent_dicts,
                                               fake_connector, audit_dir):
        result = run(
            date="2026-05-22",
            mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        assert "exp1220" in result.per_stream_counts
        assert result.per_stream_counts["exp1220"]["allow"] == 1
        assert result.per_stream_counts["exp1220"]["sized"] == 1
        # gld_cal had action=HOLD → blocked
        assert result.per_stream_counts["gld_cal"]["block"] == 1
        assert result.per_stream_counts["gld_cal"]["sized"] == 0

    def test_audit_files_written(self, sample_intent_dicts, fake_connector,
                                        audit_dir):
        run(
            date="2026-05-22",
            mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        root = Path(audit_dir) / "2026-05-22"
        for name in ("01_intent.jsonl", "02_gated.jsonl",
                       "03_sized.jsonl", "04_orders.jsonl"):
            assert (root / name).exists(), f"missing {name}"

        intent_rows = [json.loads(l) for l in (root / "01_intent.jsonl").read_text().splitlines()]
        assert len(intent_rows) == 8
        assert {r["stream"] for r in intent_rows} == {
            "exp1220", "xlf_cs", "xli_cs", "qqq_cs",
            "gld_cal", "slv_cal", "cross_vol", "v5_hedge",
        }

        orders_rows = [json.loads(l) for l in (root / "04_orders.jsonl").read_text().splitlines()]
        assert len(orders_rows) == 6


class TestRunFailureModes:
    def test_invalid_mode_returns_failed(self):
        result = run(date="2026-05-22", mode="bogus")
        assert result.status == "FAILED"
        assert any("invalid mode" in e for e in result.errors)

    def test_live_mode_without_token_refuses(self, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_CONFIRM_LIVE_TOKEN", raising=False)
        monkeypatch.setenv("ALPACA_PAPER", "false")
        result = run(date="2026-05-22", mode="live")
        assert result.status == "FAILED"
        assert any("live mode requires" in e for e in result.errors)

    def test_live_mode_with_paper_env_refuses(self, monkeypatch):
        monkeypatch.setenv("ORCHESTRATOR_CONFIRM_LIVE_TOKEN", "yes")
        monkeypatch.setenv("ALPACA_PAPER", "true")  # paper still on
        result = run(date="2026-05-22", mode="live")
        assert result.status == "FAILED"

    def test_signal_generator_exception_fails_closed(self, fake_connector,
                                                            audit_dir):
        def _boom(_date):
            raise RuntimeError("upstream down")

        result = run(
            date="2026-05-22",
            mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_boom,
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        assert result.status == "FAILED"
        assert any("signal_generator failed" in e for e in result.errors)
        assert result.n_intents == 0

    def test_gate_exception_fails_closed(self, sample_intent_dicts,
                                                fake_connector, audit_dir):
        def _boom(*_a, **_kw):
            raise ValueError("calendar missing")

        result = run(
            date="2026-05-22",
            mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_boom,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        assert result.status == "FAILED"
        assert result.n_intents == 8        # got that far
        assert result.n_sized == 0          # didn't get past gate

    def test_paper_mode_with_broken_snapshot_fails_closed(self, sample_intent_dicts):
        from compass.orchestrator.tests.conftest import FakeConnector
        broken = FakeConnector(snapshot_error="auth_failed")
        result = run(
            date="2026-05-22",
            mode="paper",
            connector=broken,
            signal_generator=_fake_signal_generator(sample_intent_dicts),
            gate_fn=_fake_gate,
            sizer_fn=_fake_sizer,
            router_fn=_fake_router,
        )
        assert result.status == "FAILED"
        assert result.n_filled == 0


class TestRunDataHandling:
    def test_malformed_dict_is_skipped_not_fatal(self, fake_connector, audit_dir):
        def _gen(_date):
            return [
                {"stream": "exp1220", "date": "2026-05-22", "ticker": "SPY",
                 "action": "OPEN", "direction": "put_credit_spread",
                 "delta": 0.18, "dte": 28, "width": 5.0,
                 "weight": 0.2, "confidence": 0.9, "notes": ""},
                {"stream": "broken"},  # missing required fields
                "not-a-dict",
            ]
        result = run(
            date="2026-05-22", mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_gen,
            gate_fn=_fake_gate, sizer_fn=_fake_sizer, router_fn=_fake_router,
        )
        assert result.status == "OK"
        assert result.n_intents == 1   # only the clean one survived

    def test_signal_intent_inputs_pass_through(self, sample_signal_intents,
                                                       fake_connector, audit_dir):
        """generate_all_signals normally emits dicts, but the pipeline
        also accepts pre-typed SignalIntents directly."""
        def _gen(_date):
            return sample_signal_intents

        result = run(
            date="2026-05-22", mode="dry-run",
            audit_dir=audit_dir,
            connector=fake_connector,
            signal_generator=_gen,
            gate_fn=_fake_gate, sizer_fn=_fake_sizer, router_fn=_fake_router,
        )
        assert result.n_intents == 8


# ───────────────────────────────────────────────────────────────────────────
# Tests — cleanup
# ───────────────────────────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_cancels_and_snapshots(self, audit_dir):
        from compass.orchestrator.tests.conftest import FakeConnector
        conn = FakeConnector(cancel_count=7)
        result = cleanup(date="2026-05-22", audit_dir=audit_dir,
                           connector=conn)
        assert result.mode == "cleanup"
        assert result.status == "OK"
        assert result.n_filled == 7              # cancelations reported here
        assert ("cancel_all",) in conn.calls
        assert any(c[0] == "snapshot" for c in conn.calls)

        log = Path(audit_dir) / "2026-05-22" / "06_cleanup.jsonl"
        assert log.exists()
        row = json.loads(log.read_text().splitlines()[0])
        assert row["canceled_orders"] == 7
        assert row["date"] == "2026-05-22"

    def test_cleanup_records_cancel_error(self, audit_dir):
        class _BrokenConn:
            calls = []
            def cancel_all(self):
                raise RuntimeError("network")
            def snapshot(self):
                from compass.orchestrator.tests.conftest import _FakeSnapshot
                return _FakeSnapshot()

        result = cleanup(date="2026-05-22", audit_dir=audit_dir,
                           connector=_BrokenConn())
        assert result.status == "FAILED"
        assert any("cancel_all failed" in e for e in result.errors)


# ───────────────────────────────────────────────────────────────────────────
# Tests — reconcile
# ───────────────────────────────────────────────────────────────────────────

class TestReconcile:
    def test_reconcile_clean(self, audit_dir):
        from compass.orchestrator.tests.conftest import FakeConnector
        diff = {
            "SPY260619P00420000": {
                "symbol": "SPY260619P00420000",
                "intended": -1, "actual": -1, "delta": 0, "status": "MATCH",
            },
        }
        conn = FakeConnector(reconcile_diff=diff)
        result = reconcile(
            date="2026-05-22", audit_dir=audit_dir,
            connector=conn,
            intended={"SPY260619P00420000": -1},
        )
        assert result.status == "OK"
        assert result.n_filled == 1
        assert result.n_rejected == 0
        assert result.per_stream_counts["_diff"]["MATCH"] == 1

        log = Path(audit_dir) / "2026-05-22" / "05_reconcile.jsonl"
        assert log.exists()
        row = json.loads(log.read_text().splitlines()[0])
        assert row["diff"]["SPY260619P00420000"]["status"] == "MATCH"

    def test_reconcile_with_orphan_returns_partial(self, audit_dir):
        from compass.orchestrator.tests.conftest import FakeConnector
        diff = {
            "AAPL260619P00150000": {
                "symbol": "AAPL260619P00150000",
                "intended": 0, "actual": -1, "delta": -1, "status": "ORPHAN",
            },
        }
        conn = FakeConnector(reconcile_diff=diff)
        result = reconcile(date="2026-05-22", audit_dir=audit_dir,
                              connector=conn, intended={})
        assert result.status == "PARTIAL"
        assert result.per_stream_counts["_diff"]["ORPHAN"] == 1

    def test_reconcile_reads_intended_from_audit(self, audit_dir):
        """If intended is None, reconcile reconstructs it from
        04_orders.jsonl. This covers the production path where the
        15:55 cron has no in-memory state from the 09:25 run."""
        root = Path(audit_dir) / "2026-05-22"
        root.mkdir(parents=True)
        (root / "04_orders.jsonl").write_text(
            json.dumps({
                "stream": "exp1220",
                "client_order_id": "x",
                "status": "FILLED",
                "contract_count": 2,
                "legs": [
                    {"symbol": "SPY260619P00420000", "side": "SELL", "quantity": 1},
                    {"symbol": "SPY260619P00415000", "side": "BUY",  "quantity": 1},
                ],
            }) + "\n"
        )

        from compass.orchestrator.tests.conftest import FakeConnector
        captured = {}

        class _RecConn(FakeConnector):
            def reconcile(self, intended, tolerance: float = 0.0):
                captured["intended"] = dict(intended)
                return {}

        conn = _RecConn()
        reconcile(date="2026-05-22", audit_dir=audit_dir, connector=conn)
        assert captured["intended"]["SPY260619P00420000"] == -2.0
        assert captured["intended"]["SPY260619P00415000"] == +2.0
