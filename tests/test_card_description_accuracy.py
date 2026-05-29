"""Regression guards for the card-description cleanup (V8A-class audit, 2026-05-28).

Locks in the corrected registry card text so each dashboard card stays
consistent with the actually-deployed strategy, and keeps the scheduler
pre-market health-check roster aligned to the active experiments.
Rationale: docs/V8A_VRP_RECON_ALL_EXP_AUDIT.md.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "experiments" / "registry.json"


def _exp(eid):
    return json.loads(REGISTRY.read_text())["experiments"][eid]


def test_exp1220_leverage_label_reconciled_to_1_1x():
    e = _exp("EXP-1220")
    assert "1.1x" in e["name"] and "3x" not in e["name"]
    assert "1.1x" in e["description"] and "1.5x" not in e["description"]


def test_exp503_describes_ml_blend_not_kelly():
    e = _exp("EXP-503")
    assert "Kelly" not in e["description"]
    assert "ML" in e["description"] and "8.5%" in e["description"]


def test_exp401_blend_is_event_gated_not_vol_regime():
    e = _exp("EXP-401")
    desc = e["description"].lower()
    assert "volatility regime detection" not in desc
    assert "fomc" in desc or "cpi" in desc


def test_exp3303b_described_spy_only():
    e = _exp("EXP-3303b")
    assert "QQQ" not in e["description"]
    assert "sector" not in e["description"].lower()
    assert "SPY" in e["description"]


def test_scheduler_premarket_roster_matches_active_experiments():
    src = (ROOT / "scheduler" / "jobs.py").read_text()
    line = next(ln for ln in src.splitlines() if "exp_ids = [" in ln)
    assert "EXP3303B" in line and "EXPV8A" in line  # added (were live but unmonitored)
    assert "EXP600" not in line                     # removed (retired)
