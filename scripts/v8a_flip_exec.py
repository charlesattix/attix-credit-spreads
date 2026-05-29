#!/usr/bin/env python3
"""V8A AUTONOMOUS FLIP EXECUTOR — runs on the Railway cron service ~13:40 UTC.

Session-independent counterpart to cc1's local wake: re-checks the gate and, on
GO, merges the pre-staged flip PR (#81: dry_run:false + status:active) via the
GitHub REST API → main updates → attix-worker auto-redeploys → V8A spawns running
VRP live. No `gh` CLI dependency (containers don't have it) — pure REST.

GATE (all must pass, else HALT and merge NOTHING — fail-closed):
  1. Account FLAT: 0 positions AND 0 open orders on PA3694QR73C1 (the 09:33 close
     must have flattened it). Uses cc5's _provider (ALPACA_*_EXPV8A env creds).
  2. PR-E #78 MERGED (the VRP engine cutover). Via GitHub REST.
  3. Prod HEALTHY: https://attix-production.up.railway.app/api/v1/health == ok.

On GO: mark PR #81 ready (it ships as a draft) then squash-merge it. Idempotent:
if #81 is already merged, that's logged as success (the flip already happened).
Always exits 0 so the cron never restart-loops; the outcome is in the logs.

Env: ALPACA_API_KEY_EXPV8A / ALPACA_API_SECRET_EXPV8A (flat check), GITHUB_TOKEN
(merge). REPO/PR are constants below.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

_here = Path(__file__).resolve()
ROOT = next((p for p in [_here, *_here.parents] if (p / "strategy" / "alpaca_provider.py").exists()), None)
if ROOT is None:
    ROOT = Path(os.environ.get("REPO_ROOT", os.getcwd()))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(_here.parent))  # so we can reuse scripts/v8a_flush.py

REPO = "charlesattix/attix-credit-spreads"
FLIP_PR = int(os.environ.get("FLIP_PR_NUMBER", "81"))
PR_E = int(os.environ.get("PR_E_NUMBER", "78"))
HEALTH_URL = "https://attix-production.up.railway.app/api/v1/health"


def log(msg: str) -> None:
    print(f"[v8a-flip] {msg}", flush=True)


def _gh(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    tok = os.environ.get("GITHUB_TOKEN", "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "User-Agent": "v8a-flip-exec", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def check_flat() -> tuple[bool, str]:
    try:
        from v8a_flush import _provider  # cc5's provider (env EXPV8A creds)
        prov = _provider()
        pos = prov.get_positions()
        orders = prov.get_orders(status="open", limit=100)
        flat = len(pos) == 0 and len(orders) == 0
        return flat, f"positions={len(pos)} open_orders={len(orders)}"
    except Exception as e:  # noqa: BLE001
        return False, f"flat-check error: {e}"


def check_pr_e_merged() -> bool:
    code, body = _gh("GET", f"/repos/{REPO}/pulls/{PR_E}")
    return code == 200 and bool(body.get("merged"))


def check_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=20) as r:
            return json.load(r).get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


def merge_flip() -> bool:
    # Already merged? (idempotent)
    code, body = _gh("GET", f"/repos/{REPO}/pulls/{FLIP_PR}")
    if code == 200 and body.get("merged"):
        log(f"PR #{FLIP_PR} already merged — flip already done.")
        return True
    if code != 200:
        log(f"cannot read PR #{FLIP_PR} (HTTP {code}): {body}")
        return False
    # Draft → mark ready first (GraphQL).
    if body.get("draft"):
        node_id = body.get("node_id")
        q = {"query": "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){clientMutationId}}",
             "variables": {"id": node_id}}
        rc, rb = _graphql(q)
        log(f"mark-ready: http={rc} errs={rb.get('errors')}")
    # Squash-merge.
    mcode, mbody = _gh("PUT", f"/repos/{REPO}/pulls/{FLIP_PR}/merge", {"merge_method": "squash"})
    if mcode == 200 and mbody.get("merged"):
        log(f"✅ PR #{FLIP_PR} MERGED ({mbody.get('sha','')[:12]}) — worker will auto-redeploy → VRP live.")
        return True
    log(f"🛑 merge failed (HTTP {mcode}): {mbody.get('message')}")
    return False


def _graphql(query: dict) -> tuple[int, dict]:
    tok = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=json.dumps(query).encode(), method="POST",
        headers={"Authorization": f"Bearer {tok}", "User-Agent": "v8a-flip-exec",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def main() -> int:
    log("=== autonomous flip gate ===")
    flat, detail = check_flat()
    merged = check_pr_e_merged()
    healthy = check_healthy()
    log(f"flat={flat} ({detail}) | PR-E#{PR_E}_merged={merged} | prod_healthy={healthy}")
    if not (flat and merged and healthy):
        reasons = []
        if not flat:
            reasons.append(f"NOT FLAT ({detail})")
        if not merged:
            reasons.append(f"PR-E #{PR_E} not merged")
        if not healthy:
            reasons.append("prod not healthy")
        log("🛑 HALT — " + "; ".join(reasons) + ". NOT merging flip; V8A stays flat+paused.")
        return 0  # exit 0: fail-closed, no restart loop
    log("✅ GO — flat + PR-E merged + healthy. Merging flip PR.")
    merge_flip()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"CRASH (fail-closed, no flip): {e}")
        sys.exit(0)
