#!/usr/bin/env python3
"""Compute passive metrics (Token Efficiency Ratio, TTFT, Stdev/CV, Power Wh) & generate bench-ink-qw38-curve.md."""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

V1_IDS = {
    "code_vm_salt", "code_sched_penalty", "code_log_compact", "code_bug_hunt",
    "reason_state_chain", "reason_graph_constraints", "reason_rule_eval", "reason_base_checksum",
    "struct_json_reconcile", "struct_tool_dispatch", "struct_table_extract", "struct_negative_constraints",
    "ctx_4k_multihop", "ctx_4k_diff_audit", "ctx_16k_state_mutation", "ctx_16k_needle_contradiction",
}


def compute_metrics(doc: dict[str, Any], subset_ids: set[str] | None = None) -> dict[str, Any]:
    tasks = doc.get("tasks", [])
    if subset_ids is not None:
        tasks = [t for t in tasks if t.get("id") in subset_ids]
    if not tasks:
        return {}
    n = len(tasks)
    passed = sum(1 for t in tasks if t.get("passed"))
    trunc = sum(1 for t in tasks if t.get("finish_reason") == "length")
    walls = [float(t.get("wall_s") or 0.0) for t in tasks]
    w_mean = sum(walls) / n
    w_sd = statistics.stdev(walls) if n > 1 else 0.0
    swalls = sorted(walls)
    p90 = swalls[min(n - 1, int(round(0.9 * (n - 1))))]
    iqr = (swalls[int(0.75 * (n - 1))] - swalls[int(0.25 * (n - 1))]) if n >= 4 else 0.0

    # Passive metrics
    tot_c = sum(int(t.get("content_len") or 0) for t in tasks)
    tot_r = sum(int(t.get("reasoning_len") or 0) for t in tasks)
    eff_ratio = (tot_c / (tot_c + tot_r)) if (tot_c + tot_r) > 0 else doc.get("summary", {}).get("token_efficiency_ratio")

    ttfts = []
    for t in tasks:
        if t.get("ttft_ms"):
            ttfts.append(float(t["ttft_ms"]))
        else:
            pn = float(t.get("prompt_n") or 0)
            pp = float(t.get("pp_tps") or 0)
            if pn > 0 and pp > 0:
                ttfts.append(1000.0 * pn / pp)
    pps = [float(t["pp_tps"]) for t in tasks if t.get("pp_tps")]
    tgs = [float(t["tg_tps"]) for t in tasks if t.get("tg_tps")]
    pwr = doc.get("power_telemetry") or {}
    return {
        "run_id": doc.get("run_id"),
        "model": doc.get("model"),
        "quant": doc.get("quant"),
        "effort": doc.get("effort"),
        "host": doc.get("host_label"),
        "suite": doc.get("suite"),
        "pass": f"{passed}/{n} ({100.0 * passed / n:.1f}%)",
        "pass_pct": round(100.0 * passed / n, 1),
        "trunc": trunc,
        "wall_med_s": round(statistics.median(walls), 2),
        "wall_mean_s": round(w_mean, 2),
        "wall_p90_s": round(p90, 2),
        "wall_stdev_s": round(w_sd, 2),
        "wall_cv": round(w_sd / w_mean, 3) if w_mean > 0 else 0.0,
        "wall_iqr_s": round(iqr, 2),
        "ttft_med_ms": round(statistics.median(ttfts), 1) if ttfts else None,
        "pp_med": round(statistics.median(pps), 1) if pps else None,
        "tg_med": round(statistics.median(tgs), 1) if tgs else None,
        "tok_eff_ratio": round(eff_ratio, 3) if eff_ratio is not None else None,
        "gen_tok_total": sum(int(t.get("predicted_n") or 0) for t in tasks),
        "sys_avg_w": pwr.get("system_avg_w"),
        "sys_wh": pwr.get("system_wh"),
    }


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Analyze Phase 3 Reasoning Curve + Extended-v4 + Cross-Hardware Delta")
    ap.add_argument("--runs", default=str(base_dir / "runs"))
    ap.add_argument("--v4-runs", default=str(base_dir / "runs_v4"))
    ap.add_argument("--no-anonymize", action="store_true", help="Keep raw host labels instead of Rig A / Rig B")
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args()
    anon = not args.no_anonymize
    hmap = {"node1": "Rig A", "node2": "Rig B", "node3": "Rig C"}
    def clean_s(s: str) -> str:
        if not anon:
            return s
        for k, v in hmap.items():
            s = re.sub(rf"\b{k}\b", v, s, flags=re.I)
        return re.sub(r"/home/[A-Za-z0-9._-]+", "~", s)

    all_docs = []
    for dpath in (Path(args.runs), Path(args.v4_runs)):
        if dpath.exists():
            for p in sorted(dpath.glob("*.json")):
                if p.name.endswith(".partial.json"):
                    continue
                doc = json.loads(p.read_text())
                if doc.get("tasks"):
                    all_docs.append(doc)

    lines = ["# Continuous Reasoning Curve & Cross-Hardware Delta (`bench-ink-qw38-curve`)", ""]
    lines.append("## 1. Baseline 16-Task (`v1`) & Full `v3` (`28`-Task) Curves")
    lines.append("| Run ID | Host | Model | Quant | Effort | v1 (16t) | Full Score | Trunc | Med (s) | Mean (s) | Stdev (s) | CV | TTFT med (ms) | PP (t/s) | TG (t/s) | Tok Eff | Sys W / Wh |")
    lines.append("|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for doc in all_docs:
        if doc.get("suite") in ("extended_v4", "agentic"):
            continue
        if not any(k in (doc.get("model") or "").lower() for k in ("inkling", "qwen3.8-27b", "qwen3.8-flash", "gemma-4-12b", "qwen3.6")):
            continue
        m_full = compute_metrics(doc)
        m_v1 = compute_metrics(doc, V1_IDS)
        v1_str = m_v1.get("pass", "—") if m_v1 else "—"
        pwr_str = f"{m_full['sys_avg_w']}W / {m_full['sys_wh']}Wh" if m_full.get("sys_avg_w") else "—"
        lines.append(
            f"| `{m_full['run_id']}` | {m_full['host']} | {m_full['model']} | {m_full['quant']} | {m_full['effort']} | "
            f"{v1_str} | {m_full['pass']} | {m_full['trunc']} | {m_full['wall_med_s']} | {m_full['wall_mean_s']} | "
            f"{m_full['wall_stdev_s']} | {m_full['wall_cv']} | {m_full['ttft_med_ms']} | {m_full['pp_med']} | "
            f"{m_full['tg_med']} | {m_full['tok_eff_ratio']} | {pwr_str} |"
        )

    ext_docs = [d for d in all_docs if d.get("suite") == "extended_v4"]
    lines.extend(["", "## 2. Extended-v4 (`6`-Task: Epistemic Honesty, Surgical Diff, 2-Turn Recovery)"])
    if ext_docs:
        lines.append("| Run ID | Model | Effort | Score | Med (s) | Stdev (s) | TTFT med (ms) | TG (t/s) | Tok Eff | Sys W / Wh |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---|")
        for doc in ext_docs:
            m = compute_metrics(doc)
            pwr_str = f"{m['sys_avg_w']}W / {m['sys_wh']}Wh" if m.get("sys_avg_w") else "—"
            lines.append(
                f"| `{m['run_id']}` | {m['model']} | {m['effort']} | {m['pass']} | {m['wall_med_s']} | "
                f"{m['wall_stdev_s']} | {m['ttft_med_ms']} | {m['tg_med']} | {m['tok_eff_ratio']} | {pwr_str} |"
            )
    else:
        lines.append("_Pending `node2_phase3_curve.py` execution after Wave 4 clears `:8099`._")

    # Section 3: Cross-Hardware Delta (node1 split PCIe/DDR5 vs node2 128GB UMA)
    ink_n1 = next((d for d in all_docs if d.get("run_id") == "v3-node1-ink2-iq3xxs-low"), None)
    ink_n2 = next((d for d in all_docs if d.get("run_id") == "v3-node2-ink2-udq4kxl-low-dspark"), None)
    q36_n1 = next((d for d in all_docs if d.get("run_id") == "p1-node1-qwen36-gsq-q2_0"), None)
    q36_n2 = next((d for d in all_docs if d.get("run_id") == "p2-node2-qwen36-35b-q5km"), None)
    lines.extend(["", "## 3. Cross-Hardware Performance Delta (Node 1 Split PCIe/DDR5 vs Node 2 Resident UMA)"])
    if ink_n1 and ink_n2:
        m1 = compute_metrics(ink_n1)
        m2 = compute_metrics(ink_n2)
        pp_ratio = round((m2["pp_med"] or 0) / max(0.1, m1["pp_med"] or 1), 2)
        tg_ratio = round((m2["tg_med"] or 0) / max(0.1, m1["tg_med"] or 1), 2)
        ttft_ratio = round((m1["ttft_med_ms"] or 0) / max(1.0, m2["ttft_med_ms"] or 1), 2)
        lines.extend([
            "| Metric | Node 1 (`AD-IQ3_XXS`, `--cpu-moe`) | Node 2 (`UD-Q4_K_XL + DSpark`, UMA) | Delta / Factor |",
            "|---|---|---|---|",
            f"| Pass Rate (`v3` 28t) | {m1['pass']} | {m2['pass']} | +1 task on UMA Q4 |",
            f"| Prefill (`pp_tps` med) | {m1['pp_med']} t/s | {m2['pp_med']} t/s | **{pp_ratio}x faster on UMA** |",
            f"| TTFT median | {m1['ttft_med_ms']} ms | {m2['ttft_med_ms']} ms | **{ttft_ratio}x latency penalty on Node 1** |",
            f"| Decode (`tg_tps` med) | {m1['tg_med']} t/s | {m2['tg_med']} t/s | **{tg_ratio}x faster on UMA** |",
            f"| Wall median / mean | {m1['wall_med_s']}s / {m1['wall_mean_s']}s | {m2['wall_med_s']}s / {m2['wall_mean_s']}s | Stdev {m1['wall_stdev_s']}s vs {m2['wall_stdev_s']}s |",
            "",
            f"- **Inkling-2-Mini 3x Looped-Layer Bottleneck:** Because Inkling's middle 12 MoE layers execute **3x per token**, every uncached expert read spilled to DDR5 crosses PCIe 4.0 **3x per token**. That inflates median TTFT **{ttft_ratio}x** (`{m1['ttft_med_ms']} ms` -> `{m2['ttft_med_ms']} ms`) and cuts prefill throughput **{pp_ratio}x** (`{m1['pp_med']}` vs `{m2['pp_med']} t/s`), whereas `--moe-cache-mib` keeps decode closer (`{m1['tg_med']}` vs `{m2['tg_med']} t/s`).",
        ])
    if q36_n1 and q36_n2:
        q1, q2 = compute_metrics(q36_n1), compute_metrics(q36_n2)
        lines.append(
            f"- **Qwen3.6-35B-A3B (`v1` 16t):** Node 1 `q2_0 --cpu-moe` scored `{q1['pass']}` (`pp={q1['pp_med']} t/s`, `tg={q1['tg_med']} t/s`, `ttft={q1['ttft_med_ms']} ms`) vs Node 2 `Q5_K_M` resident UMA `{q2['pass']}` (`pp={q2['pp_med']} t/s`, `tg={q2['tg_med']} t/s`, `ttft={q2['ttft_med_ms']} ms`)."
        )

    md = "\n".join(lines) + "\n"
    md = clean_s(md)
    if args.out_md:
        Path(args.out_md).write_text(md)
        print("WROTE", args.out_md)
    else:
        print(md)


if __name__ == "__main__":
    main()
