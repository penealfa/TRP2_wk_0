"""Summarise runs.jsonl into the per-stage table for COSTS.md.

    python -m pipeline.costs            # prints a Markdown table
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from pipeline.common import RUNS_LOG, main_guard


def main() -> int:
    if not RUNS_LOG.exists():
        print("runs.jsonl is empty: run some stages first")
        return 0
    rows = [json.loads(l) for l in RUNS_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    by = defaultdict(list)
    for r in rows:
        by[(r.get("stage"), r.get("provider"), r.get("model"))].append(r)
    print("| Stage | Provider | Model | Calls (ok / failed) | Median latency s | GPU min | Cost $ | List price $ |")
    print("|---|---|---|---|---|---|---|---|")
    for (stage, prov, model), rs in sorted(by.items(), key=lambda kv: str(kv[0])):
        ok = [r for r in rs if r.get("status") == "ok"]
        lat = statistics.median([r["latency_s"] for r in ok]) if ok else 0
        gpu = sum(r.get("gpu_s") or 0 for r in rs) / 60
        cost = sum(r.get("cost_usd") or 0 for r in rs)
        lp = sum(r.get("list_price_usd") or 0 for r in rs)
        print(f"| {stage} | {prov} | {model} | {len(ok)} / {len(rs) - len(ok)} | {lat:.1f} | {gpu:.1f} | {cost:.2f} | {lp:.2f} |")
    return 0


if __name__ == "__main__":
    main_guard(main)
