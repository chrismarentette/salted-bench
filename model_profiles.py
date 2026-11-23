#!/usr/bin/env python3
"""Inspect model-profiles.json vs existing runs/ to build a prioritized idle-window shakedown queue."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Show prioritized frontier shakedown & 16k-ext backfill queue")
    ap.add_argument("--runs", default=str(BASE_DIR / "runs"))
    ap.add_argument("--profiles", default=str(BASE_DIR / "model-profiles.json"))
    args = ap.parse_args()

    profiles = json.loads(Path(args.profiles).read_text())["models"]
    runs = []
    for p in sorted(Path(args.runs).glob("*.json")):
        d = json.loads(p.read_text())
        d["_file"] = p.name
        runs.append(d)

    print("=== PRIORITIZED IDLE-WINDOW SHAKEDOWN PLAN (ZERO SWEEPS ON TIER=small) ===")
    for model_name, cfg in profiles.items():
        tier = cfg["tier"]
        matching = [r for r in runs if r.get("model", "").lower().startswith(model_name.lower()[:10])]
        if tier == "small":
            v3_ok = any(r.get("suite") == "v3" and r.get("summary", {}).get("total_tasks", 0) >= 28 for r in matching)
            print(f"[SMALL   ] {model_name:22s} | v3_28={'OK' if v3_ok else 'MISSING'} | sweeps=SKIPPED (policy)")
            continue

        actions = []
        # Check for high 8k truncation candidates for 16k output pass (within existing ctx)
        for r in matching:
            s = r.get("summary", {})
            tr = s.get("truncated", 0)
            max_tok = r.get("max_tokens", 8192)
            if max_tok <= 8192 and tr >= (2 if tier == "frontier" else 3) and s.get("total_tasks", 0) >= 16:
                actions.append(f"16k-ext on {r['_file']} ({tr}/{s['total_tasks']} truncated @ 8k)")
        # Check missing reasoning levels on v3 full
        if tier == "frontier":
            efforts_done = {str(r.get("effort", "")).lower() for r in matching if r.get("summary", {}).get("total_tasks", 0) >= 28}
            for lvl in cfg.get("reasoning_levels", []):
                lbl = {True: "think", False: "nothink"}.get(lvl, str(lvl)).lower()
                if lbl not in efforts_done:
                    actions.append(f"v3 full @ {cfg['reasoning_control']}={lvl}")
            if cfg.get("card_recommended", {}).get("presence_penalty", 0.0) == 0.0:
                if any((r.get("presence_penalty") or 0) > 0 for r in matching):
                    actions.append("card-sampler audit (presence_penalty=0.0, temp=0.6 vs 1.0)")
            actions.append("Agentic v4 (salted-agentic.py)")

        print(f"[{tier.upper():8s}] {model_name:22s} | runs={len(matching):2d} | next: {', '.join(actions) if actions else 'COMPLETE'}")


if __name__ == "__main__":
    main()
