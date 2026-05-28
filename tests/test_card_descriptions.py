"""Acceptance: every active experiment renders a non-empty card description,
sourced from the registry (single source of truth — no hardcoded dict)."""
import json
from pathlib import Path

from web_dashboard.data import query_experiment
from web_dashboard.html import _render_exp_card

REGISTRY = Path(__file__).resolve().parent.parent / "experiments" / "registry.json"


def _active():
    d = json.loads(REGISTRY.read_text())
    exps = d if isinstance(d, list) else d.get("experiments", list(d.values()))
    exps = exps if isinstance(exps, list) else list(exps.values())
    return [e for e in exps if e.get("status") == "active"]


def test_every_active_experiment_has_registry_description():
    missing = [e["id"] for e in _active() if not (e.get("description") or "").strip()]
    assert missing == [], f"active experiments missing a description: {missing}"


def test_query_experiment_propagates_description_to_row():
    # The dashboard row (what the card renders from) must carry the registry desc.
    for e in _active():
        row = query_experiment(e)
        assert (row.get("description") or "").strip(), f"{e['id']} row has no description"
        assert row["description"] == e["description"]


def test_card_renders_registry_description_no_hardcoded_dict():
    e = next(x for x in _active() if x["id"] == "EXP-V8A")
    row = query_experiment(e)
    row["alpaca"] = {"equity": 101000.0, "positions": []}
    row["data_source"] = "live"
    row["data_age_seconds"] = 5
    html = _render_exp_card(row)
    assert 'class="exp-desc"' in html
    assert "volatility risk premium" in html.lower()        # description rendered
    assert "Variance Risk Premium Multi-Stream" in html     # canonical name rendered


def test_v8a_canonical_metadata():
    v8a = next(x for x in _active() if x["id"] == "EXP-V8A")
    assert v8a["name"] == "Variance Risk Premium Multi-Stream"
    assert "Maximus" not in v8a["name"]
    assert "Maximus" not in v8a["description"]
    assert "Sharpe 6.39" in v8a["description"]
    assert v8a["ticker"] == "SPY" and v8a["created_by"] == "carlos"


def test_hardcoded_descriptions_dict_removed():
    src = (Path(__file__).resolve().parent.parent / "web_dashboard" / "html.py").read_text()
    assert "_EXP_DESCRIPTIONS: dict" not in src   # dict definition gone (registry is source)
