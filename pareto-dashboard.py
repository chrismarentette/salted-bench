#!/usr/bin/env python3
# JIG: tier=drawer | mode=mutates | nodes=node1,node2 | tags=benchmark,pareto,dashboard,html,visualization
# SUMMARY: Build single-file interactive Pareto Frontier HTML dashboard from salted-eval/v1 JSON runs.
# USE: Run with `--runs-dir <dir> --out <dashboard.html> [--anonymize]`; `--selftest` validates offline.
"""pareto-dashboard.py — Self-contained HTML telemetry generator for salted-eval/v1 runs.

Adapts the dark Pareto Frontier Telemetry Dashboard into a data-driven,
zero-dependency single HTML file suitable for local analysis or sharing on
forums/Discord (`--anonymize` strips internal hostnames, local paths, and IPs).
"""

import argparse
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

MODEL_FAMILY_PALETTES = {
    "inkling": ["#38bdf8", "#3b82f6", "#60a5fa", "#0ea5e9", "#7dd3fc", "#2563eb"],
    "qwflash": ["#ef4444", "#f87171", "#dc2626", "#b91c1c"],
    "qw35":    ["#fbbf24", "#f59e0b", "#fcd34d", "#d97706"],
    "qw38":    ["#a78bfa", "#c084fc", "#8b5cf6", "#7c3aed"],
    "qw36":    ["#fb923c", "#f97316", "#fdba74", "#ea580c"],
    "gemma":   ["#34d399", "#22c55e", "#10b981", "#4ade80", "#059669"],
    "lfm":     ["#e2e8f0", "#cbd5e1", "#f8fafc", "#94a3b8"],
    "phi":     ["#eeff00", "#e2f700", "#f5ff4d", "#d4e800"],
    "qw3":     ["#f472b6", "#ec4899", "#f9a8d4", "#db2777"],
    "other":   ["#64748b", "#94a3b8", "#475569"],
}

HOST_ANON_MAP = {
    "node1": "Rig A (8 GB CUDA)",
    "node2": "Rig B (128 GB Strix Halo)",
    "node3": "Rig C (Test Bench)",
}
HOST_SLUG_MAP = {
    "node1": "rig-a",
    "node2": "rig-b",
    "node3": "rig-c",
}


def detect_model_family(model_name: str) -> tuple[str, str]:
    m = (model_name or "").lower()
    if "inkling" in m:
        return "inkling", "Inkling-2-Mini"
    if "flash" in m:
        return "qwflash", "QFN"
    if "122b" in m:
        return "qw35", "Qwen3.5-122B-A10B"
    if "3.8" in m or "27b" in m or "qw38" in m:
        return "qw38", "Qwen3.8-27B"
    if "3.6" in m or "35b" in m or "qw36" in m:
        return "qw36", "Qwen3.6-35B-A3B"
    if "gemma" in m:
        return "gemma", "Gemma 4"
    if "lfm" in m:
        return "lfm", "LFM 2.5 / 3"
    if "phi" in m:
        return "phi", "Phi-4 Mini"
    if "qwen3-4b" in m or "qwen3.5" in m:
        return "qw3", "Qwen3 / 3.5 Small"
    return "other", (model_name or "Other")


def anonymize_text(text: str, slug_hosts: bool = False) -> str:
    if not text:
        return text
    s = text
    s = re.sub(r"/home/[A-Za-z0-9._-]+", "~", s)
    s = re.sub(r"/Users/[A-Za-z0-9._-]+", "~", s)
    extra_words = [w.strip() for w in (os.environ.get("BENCH_REDACT_WORDS") or "").split(",") if w.strip()]
    for w in extra_words:
        s = re.sub(rf"\b{re.escape(w)}\b", "redacted-host", s, flags=re.I)
    s = re.sub(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", "<internal-ip>", s)
    s = re.sub(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<tailnet-ip>", s)
    s = re.sub(r"https?://(?:127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|100\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+)(?::\d+)?\S*", "<local-endpoint>", s)
    hmap = HOST_SLUG_MAP if slug_hosts else HOST_ANON_MAP
    for host, alias in hmap.items():
        s = re.sub(rf"\b{re.escape(host)}\b", alias, s, flags=re.I)
    return s


def normalize_run(raw: dict, fam_counters: dict, anonymize: bool = False) -> dict:
    run = json.loads(json.dumps(raw))
    if anonymize:
        run["host_label"] = HOST_ANON_MAP.get(run.get("host_label", ""), anonymize_text(run.get("host_label", "Lab Rig")))
        for k in ("hardware", "engine", "backend", "notes", "mem_label", "phase", "insight"):
            if isinstance(run.get(k), str):
                run[k] = anonymize_text(run[k])
        if isinstance(run.get("extended_from"), str):
            run["extended_from"] = anonymize_text(run["extended_from"], slug_hosts=True)
        run.pop("url", None)
        if isinstance(run.get("llama_version"), dict):
            lv = run["llama_version"]
            lv.pop("origin", None)
            if isinstance(lv.get("binary_path"), str):
                lv["binary_path"] = anonymize_text(lv["binary_path"])
            if isinstance(lv.get("model_alias"), str):
                lv["model_alias"] = anonymize_text(lv["model_alias"])
        if isinstance(run.get("power_telemetry"), dict):
            pt = run["power_telemetry"]
            if isinstance(pt.get("host"), str):
                pt["host"] = HOST_SLUG_MAP.get(pt["host"], anonymize_text(pt["host"], slug_hosts=True))
        if isinstance(run.get("run_validity"), dict):
            rv = run["run_validity"]
            if isinstance(rv.get("reason"), str):
                rv["reason"] = anonymize_text(rv["reason"])
        if isinstance(run.get("perf_validation"), dict) and isinstance(run["perf_validation"].get("warnings"), list):
            run["perf_validation"]["warnings"] = [anonymize_text(str(w)) for w in run["perf_validation"]["warnings"]]
        for t in run.get("tasks") or []:
            if isinstance(t.get("detail"), str):
                t["detail"] = anonymize_text(t["detail"])
        run["run_id"] = anonymize_text(run.get("run_id", f"run-{sum(fam_counters.values())}"), slug_hosts=True)
    if not run.get("suite"):
        tot = (run.get("summary") or {}).get("total_tasks") or len(run.get("tasks") or [])
        if tot == 24:
            run["suite"] = "v2"
        elif tot >= 28:
            run["suite"] = "v3"
        else:
            run["suite"] = "v1"
    if not run.get("subset"):
        run["subset"] = "all"
    rid_l = (run.get("run_id") or "").lower()
    if not run.get("draft_model") and ("-mtp" in rid_l or "-dspark" in rid_l):
        run["draft_model"] = "dspark" if "-dspark" in rid_l else "mtp"
    if not run.get("categories") and run.get("tasks"):
        cats: dict[str, dict] = {}
        by_cat: dict[str, list[dict]] = {}
        for t in run["tasks"]:
            ck = t.get("category") or "general"
            by_cat.setdefault(ck, []).append(t)
        for ck, items in by_cat.items():
            walls = sorted(float(t.get("wall_s") or 0.0) for t in items)
            mid = len(walls) // 2
            med_w = walls[mid] if len(walls) % 2 else (walls[mid - 1] + walls[mid]) / 2.0
            pc = sum(1 for t in items if t.get("passed"))
            cats[ck] = {
                "pass_count": pc,
                "total": len(items),
                "pass_pct": round(100.0 * pc / max(1, len(items)), 1),
                "wall_median_s": round(med_w, 2),
            }
        run["categories"] = cats
    fam_key, fam_label = detect_model_family(run.get("model", ""))
    idx = fam_counters.get(fam_key, 0)
    fam_counters[fam_key] = idx + 1
    pal = MODEL_FAMILY_PALETTES[fam_key]
    run["family_key"] = fam_key
    run["family_label"] = fam_label
    # Coherent model-family color + sub-shades per run
    run["color"] = pal[idx % len(pal)]
    if not run.get("shape"):
        if run.get("subset", "all") != "all":
            run["shape"] = "diamond"
        elif run.get("draft_model"):
            run["shape"] = "hex"
        else:
            run["shape"] = "circle"
    return run


def is_supported_run(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("schema") == "salted-eval/v1":
        return True
    return item.get("schema_version") == 1 and item.get("suite") in ("extended_v4", "agentic")


def load_runs(inputs: list[Path], anonymize: bool = False) -> list[dict]:
    files: list[Path] = []
    for p in inputs:
        if p.is_dir():
            for jf in sorted(p.glob("*.json")):
                if jf.name.endswith(".partial.json"):
                    continue
                files.append(jf)
        elif p.is_file():
            files.append(p)
    runs = []
    fam_counters: dict[str, int] = {}
    for fp in files:
        data = json.loads(fp.read_text())
        if isinstance(data, list):
            for item in data:
                if is_supported_run(item):
                    runs.append(normalize_run(item, fam_counters, anonymize))
        elif is_supported_run(data):
            runs.append(normalize_run(data, fam_counters, anonymize))
    return runs


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>__TITLE__</title>
  <style>
    :root {
      --bg-base: #0b1120;
      --bg-card: #111827;
      --bg-card-alt: #0f172a;
      --bg-elevated: #1e293b;
      --border-subtle: #1f2937;
      --border-strong: #334155;
      --text-primary: #f1f5f9;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --cyan: #38bdf8;
      --cyan-dim: rgba(56, 189, 248, 0.14);
      --amber: #fb923c;
      --emerald: #34d399;
      --emerald-dim: rgba(52, 211, 153, 0.14);
      --violet: #a78bfa;
      --rose: #fb7185;
      --font-sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --shadow-card: 0 10px 30px -10px rgba(0, 0, 0, 0.65);
      --shadow-tooltip: 0 16px 40px -8px rgba(0, 0, 0, 0.85);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-base);
      background-image:
        radial-gradient(circle at 12% 0%, rgba(56, 189, 248, 0.06), transparent 40%),
        radial-gradient(circle at 88% 10%, rgba(167, 139, 250, 0.05), transparent 38%);
      color: var(--text-primary);
      font-family: var(--font-sans);
      line-height: 1.5;
      min-height: 100vh;
      -webkit-font-smoothing: antialiased;
    }
    .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
    .app-shell { max-width: 1440px; margin: 0 auto; padding: 20px 20px 44px; }
    .header {
      display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
      gap: 16px; padding-bottom: 16px; margin-bottom: 16px; border-bottom: 1px solid var(--border-subtle);
    }
    .header-title-group { display: flex; flex-direction: column; gap: 6px; }
    .eyebrow {
      display: inline-flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 600;
      letter-spacing: 0.08em; text-transform: uppercase; color: var(--cyan);
    }
    .eyebrow-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
    h1 { font-size: clamp(1.35rem, 2.2vw, 1.75rem); font-weight: 700; letter-spacing: -0.02em; }
    .subtitle { font-size: 13px; color: var(--text-secondary); display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .meta-chip {
      display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px;
      background: var(--bg-card); border: 1px solid var(--border-subtle); font-size: 11.5px; color: var(--text-secondary);
    }
    .header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .btn {
      display: inline-flex; align-items: center; justify-content: center; gap: 7px;
      padding: 8px 13px; border-radius: var(--radius-sm); font-size: 12.5px; font-weight: 600;
      cursor: pointer; border: 1px solid var(--border-strong); background: var(--bg-card);
      color: var(--text-primary); transition: all 0.16s ease; font-family: var(--font-sans);
    }
    .btn:focus-visible, .pill:focus-visible, .tab-btn:focus-visible, .drawer-toggle:focus-visible, .run-chip:focus-visible, .zoom-reset-btn:focus-visible {
      outline: 2px solid var(--cyan); outline-offset: 2px;
    }
    .btn:hover { background: var(--bg-elevated); border-color: var(--cyan); color: #fff; }
    .btn-primary { background: rgba(56, 189, 248, 0.14); border-color: rgba(56, 189, 248, 0.45); color: var(--cyan); }
    .btn-emerald { background: rgba(52, 211, 153, 0.14); border-color: rgba(52, 211, 153, 0.45); color: var(--emerald); }
    .btn-ghost-sm { padding: 3px 8px; font-size: 11px; border-color: var(--border-subtle); color: var(--text-secondary); }

    .kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
    .kpi-card {
      background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md);
      padding: 11px 14px; display: flex; flex-direction: column; gap: 3px;
    }
    .kpi-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
    .kpi-value { font-size: 18px; font-weight: 700; letter-spacing: -0.02em; }
    .kpi-sub { font-size: 11px; color: var(--text-secondary); }

    .filter-panel {
      background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-md);
      margin-bottom: 16px; overflow: hidden;
    }
    .filter-suite-bar {
      padding: 9px 14px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 10px; border-bottom: 1px solid var(--border-subtle); background: rgba(15, 23, 42, 0.72);
    }
    .suite-explain { font-size: 11.5px; color: var(--text-secondary); }
    .filter-rig-bar {
      padding: 8px 14px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 10px; border-bottom: 1px solid var(--border-subtle); background: rgba(15, 23, 42, 0.52);
    }
    .filter-top-bar {
      padding: 8px 14px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 10px; background: rgba(15, 23, 42, 0.36);
    }
    .filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
    .pills-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin-right: 4px; }
    .pill {
      padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-subtle);
      background: var(--bg-card-alt); color: var(--text-secondary); font-size: 11.5px; font-weight: 500;
      cursor: pointer; transition: all 0.15s ease; font-family: var(--font-sans);
    }
    .pill:hover { border-color: var(--border-strong); color: var(--text-primary); }
    .pill.active { background: var(--cyan-dim); border-color: rgba(56, 189, 248, 0.6); color: var(--cyan); font-weight: 600; }

    .drawer-toggle {
      display: inline-flex; align-items: center; gap: 8px; padding: 5px 11px; border-radius: var(--radius-sm);
      border: 1px solid var(--border-strong); background: var(--bg-card-alt); color: var(--text-primary);
      font-size: 12px; font-weight: 600; cursor: pointer; font-family: var(--font-sans);
    }
    .drawer-toggle:hover { border-color: var(--cyan); }
    .drawer-hint { font-size: 11px; color: var(--text-muted); font-weight: 400; }
    .runs-drawer {
      border-top: 1px solid var(--border-subtle); padding: 12px 14px;
      display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 12px;
      background: rgba(11, 17, 32, 0.45);
    }
    .runs-drawer.collapsed { display: none; }
    .model-group {
      background: var(--bg-card-alt); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm);
      padding: 10px 12px; display: flex; flex-direction: column; gap: 8px;
    }
    .model-group-head {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding-bottom: 6px; border-bottom: 1px solid var(--border-subtle);
    }
    .model-group-title { display: inline-flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 700; }
    .group-actions { display: inline-flex; gap: 4px; }
    .model-chips { display: flex; flex-direction: column; gap: 5px; }
    .run-chip {
      display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 5px 9px;
      border-radius: var(--radius-sm); border: 1px solid var(--border-subtle); background: rgba(15, 23, 42, 0.7);
      color: var(--text-muted); font-size: 11.5px; cursor: pointer; text-align: left; transition: all 0.14s ease;
    }
    .run-chip:hover { border-color: var(--border-strong); color: var(--text-primary); }
    .run-chip.active { color: var(--text-primary); border-color: var(--chip-color, var(--cyan)); background: rgba(30, 41, 59, 0.65); }
    .run-chip-left { display: inline-flex; align-items: center; gap: 7px; min-width: 0; }
    .run-chip-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; background: var(--chip-color, var(--cyan)); opacity: 0.28; }
    .run-chip.active .run-chip-dot { opacity: 1; box-shadow: 0 0 6px var(--chip-color, var(--cyan)); }
    .run-chip-meta { font-size: 10.5px; color: var(--text-secondary); white-space: nowrap; }

    .workspace { display: flex; flex-direction: column; gap: 18px; }
    .dock-grid { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(320px, 2fr); gap: 18px; align-items: start; }
    .card {
      background: var(--bg-card); border: 1px solid var(--border-subtle); border-radius: var(--radius-lg);
      box-shadow: var(--shadow-card); overflow: hidden;
    }
    .card-header {
      padding: 12px 18px; border-bottom: 1px solid var(--border-subtle);
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 12px; background: rgba(15, 23, 42, 0.65);
    }
    .tabs {
      display: inline-flex; background: var(--bg-base); padding: 3px;
      border-radius: var(--radius-sm); border: 1px solid var(--border-subtle); gap: 3px;
    }
    .tab-btn {
      padding: 6px 12px; border: none; background: transparent; color: var(--text-secondary);
      font-size: 12.5px; font-weight: 600; border-radius: 4px; cursor: pointer; font-family: var(--font-sans);
    }
    .tab-btn:hover { color: var(--text-primary); }
    .tab-btn.active { background: var(--bg-elevated); color: var(--text-primary); box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
    .pills-bar {
      padding: 9px 18px; border-bottom: 1px solid var(--border-subtle);
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 10px; background: rgba(11, 17, 32, 0.35);
    }
    .pills-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .chart-stage { padding: 10px 14px 6px; position: relative; background: var(--bg-card-alt); }
    #pareto-svg { width: 100%; height: auto; display: block; user-select: none; touch-action: pan-y; }
    #pareto-svg.zoomed { cursor: grab; }
    #pareto-svg.dragging { cursor: grabbing; }
    .zoom-corner { position: absolute; top: 18px; right: 24px; z-index: 20; display: none; align-items: center; gap: 6px; }
    .zoom-corner.visible { display: inline-flex; }
    .zoom-reset-btn {
      display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 999px;
      border: 1px solid rgba(56, 189, 248, 0.55); background: rgba(11, 17, 32, 0.92); color: var(--cyan);
      font-size: 11.5px; font-weight: 700; cursor: pointer; backdrop-filter: blur(6px);
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.5); transition: all 0.14s ease;
    }
    .zoom-reset-btn:hover { background: rgba(56, 189, 248, 0.18); color: #fff; border-color: var(--cyan); }
    .svg-point { cursor: pointer; transition: opacity 0.14s ease, filter 0.14s ease; }
    .svg-point.dimmed { opacity: 0.62; }
    .svg-point.active, .svg-point:hover { opacity: 1 !important; filter: drop-shadow(0 0 8px rgba(255, 255, 255, 0.5)); }
    .chart-footer { padding: 11px 18px 14px; border-top: 1px solid var(--border-subtle); display: flex; flex-direction: column; gap: 10px; }
    .legend-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text-secondary); }
    .legend-item {
      display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; border-radius: 999px;
      border: 1px solid rgba(51, 65, 85, 0.55); background: rgba(15, 23, 42, 0.65);
      color: var(--text-secondary); font-size: 11.5px; font-family: var(--font-sans);
    }
    .legend-btn { cursor: pointer; transition: all 0.14s ease; }
    .legend-btn:hover, .legend-btn:focus-visible { border-color: var(--cyan); color: var(--text-primary); background: rgba(30, 41, 59, 0.9); outline: none; }
    .legend-btn.pinned { border-color: rgba(56, 189, 248, 0.65); color: var(--text-primary); }
    .legend-rig { font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); }
    .legend-swatch { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
    .legend-diamond { width: 7px; height: 7px; transform: rotate(45deg); display: inline-block; border-radius: 1px; flex-shrink: 0; }
    .legend-dash { width: 18px; height: 2px; background: linear-gradient(90deg, var(--cyan), #60a5fa); border-radius: 2px; display: inline-block; }

    .table-wrap {
      overflow: auto; max-height: clamp(280px, 54vh, 540px);
      border-top: 1px solid var(--border-subtle);
      scrollbar-width: thin; scrollbar-color: var(--border-strong) var(--bg-card-alt);
    }
    .table-wrap::-webkit-scrollbar { width: 8px; height: 8px; }
    .table-wrap::-webkit-scrollbar-track { background: var(--bg-card-alt); }
    .table-wrap::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 999px; }
    table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
    th {
      text-align: left; padding: 9px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--text-muted); background: #0f172a; border-bottom: 1px solid var(--border-subtle); white-space: nowrap;
      position: sticky; top: 0; z-index: 3; box-shadow: 0 4px 10px -4px rgba(0, 0, 0, 0.65);
    }
    td {
      padding: 9px 12px; border-bottom: 1px solid var(--border-subtle); color: var(--text-primary);
      white-space: nowrap; background: var(--bg-card);
    }
    tbody tr:hover td { background: #162033; }
    .table-wrap th:first-child,
    .table-wrap td:first-child {
      position: sticky; left: 0; z-index: 2;
      border-right: 1px solid var(--border-strong);
      box-shadow: 6px 0 14px -4px rgba(0, 0, 0, 0.72);
    }
    .table-wrap thead th:first-child { z-index: 5; background: #0f172a; top: 0; left: 0; }
    @media (max-width: 720px) {
      .table-wrap th:first-child, .table-wrap td:first-child {
        max-width: 54vw; overflow: hidden; text-overflow: ellipsis;
      }
    }
    tr:last-child td { border-bottom: none; }
    .badge-win { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; background: var(--cyan-dim); color: var(--cyan); margin-left: 6px; }
    .badge-pass { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10.5px; font-weight: 700; background: var(--emerald-dim); color: var(--emerald); }
    .badge-fail { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10.5px; font-weight: 700; background: rgba(251, 113, 133, 0.16); color: var(--rose); }
    .badge-trunc { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10.5px; font-weight: 700; background: rgba(251, 146, 60, 0.16); color: var(--amber); }

    .dock { display: flex; flex-direction: column; gap: 16px; }
    .dock-body { padding: 14px 15px; display: flex; flex-direction: column; gap: 11px; }
    .dock-title { font-size: 13.5px; font-weight: 700; display: flex; align-items: center; justify-content: space-between; width: 100%; }
    .status-pill {
      font-size: 10.5px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
      background: var(--emerald-dim); color: var(--emerald); border: 1px solid rgba(52, 211, 153, 0.35);
      text-transform: uppercase; letter-spacing: 0.05em;
    }
    .form-section { border-top: 1px solid var(--border-subtle); padding-top: 11px; display: flex; flex-direction: column; gap: 9px; }
    .form-heading { font-size: 12px; font-weight: 700; color: var(--emerald); display: flex; align-items: center; justify-content: space-between; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field.full { grid-column: 1 / -1; }
    .field label { font-size: 11px; color: var(--text-secondary); font-weight: 500; }
    .field input {
      background: var(--bg-base); border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
      padding: 6px 9px; color: var(--text-primary); font-size: 12px; font-family: var(--font-mono); outline: none; width: 100%;
    }
    .field input:focus { border-color: var(--emerald); }
    .form-msg { font-size: 11.5px; color: var(--rose); min-height: 15px; }
    .custom-list { display: flex; flex-direction: column; gap: 6px; }
    .custom-item {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      background: rgba(52, 211, 153, 0.08); border: 1px solid rgba(52, 211, 153, 0.3);
      border-radius: var(--radius-sm); padding: 7px 10px; font-size: 11.5px;
    }
    .remove-btn { background: transparent; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 2px 5px; }

    .tooltip {
      position: fixed; z-index: 9999; pointer-events: none; opacity: 0; transform: translateY(4px);
      transition: opacity 0.12s ease, transform 0.12s ease; background: rgba(15, 23, 42, 0.97);
      border: 1px solid var(--border-strong); border-radius: var(--radius-md); padding: 12px 14px;
      width: 330px; box-shadow: var(--shadow-tooltip); backdrop-filter: blur(8px);
    }
    .tooltip.visible { opacity: 1; transform: translateY(0); }
    .tt-header {
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding-bottom: 8px; margin-bottom: 8px; border-bottom: 1px solid var(--border-subtle);
    }
    .tt-title { font-size: 12.5px; font-weight: 700; color: var(--text-primary); }
    .tt-tag { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase; }
    .tt-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 11.5px; }
    .tt-k { color: var(--text-muted); }
    .tt-v { color: var(--text-primary); text-align: right; font-family: var(--font-mono); font-weight: 600; }
    .tt-summary {
      margin-top: 9px; padding: 7px 9px; border-radius: var(--radius-sm);
      background: rgba(11, 17, 32, 0.72); border-left: 3px solid var(--cyan);
      font-size: 11.5px; line-height: 1.42; color: var(--text-secondary);
    }

    .toast {
      position: fixed; bottom: 22px; right: 22px; background: var(--bg-elevated); border: 1px solid var(--cyan);
      color: var(--text-primary); padding: 10px 16px; border-radius: var(--radius-md); font-size: 12.5px;
      font-weight: 600; box-shadow: var(--shadow-tooltip); opacity: 0; transform: translateY(10px);
      pointer-events: none; transition: all 0.2s ease; z-index: 10000;
    }
    .toast.show { opacity: 1; transform: translateY(0); }

    @media (max-width: 1120px) {
      .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .dock-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 600px) {
      .app-shell { padding: 14px 12px 28px; }
      .kpi-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <header class="header">
      <div class="header-title-group">
        <div class="eyebrow"><span class="eyebrow-dot"></span>Salted Anti-Contamination Benchmark System</div>
        <h1>__TITLE__</h1>
        <div class="subtitle">
          <span class="meta-chip mono">Salted Anti-Contamination Suites</span>
          <span class="meta-chip mono">cache_prompt: false</span>
          <span class="meta-chip">__SUBTITLE__</span>
        </div>
      </div>
      <div class="header-actions">
        <button id="export-csv-btn" class="btn" type="button">Export CSV</button>
        <button id="export-png-btn" class="btn btn-primary" type="button">Export PNG (2×)</button>
      </div>
    </header>

    <section class="kpi-grid" id="kpi-strip"></section>

    <section class="filter-panel" aria-label="Benchmark Suite, Rig, Model Family & Run Filters">
      <div class="filter-suite-bar">
        <div class="filter-row" id="suite-filters"></div>
        <div class="suite-explain" id="suite-explain"></div>
      </div>
      <div class="filter-rig-bar">
        <div class="filter-row" id="system-filters"></div>
        <button class="drawer-toggle" id="drawer-toggle-btn" type="button" aria-expanded="false">
          <span id="drawer-summary-text">Runs</span>
          <span class="drawer-hint" id="drawer-hint-text">Toggle ▾</span>
        </button>
      </div>
      <div class="filter-top-bar">
        <div class="filter-row" id="model-quick-filters"></div>
      </div>
      <div class="runs-drawer collapsed" id="runs-drawer"></div>
    </section>

    <main class="workspace">
      <section class="card">
        <div class="card-header">
          <div class="tabs" role="tablist">
            <button class="tab-btn active" id="tab-overall" type="button">Pareto Chart</button>
            <button class="tab-btn" id="tab-category" type="button">By Category</button>
            <button class="tab-btn" id="tab-matrix" type="button">Task Matrix</button>
          </div>
          <div class="mono" style="font-size: 11.5px; color: var(--text-muted);" id="chart-context-tag"></div>
        </div>

        <div class="pills-bar" id="controls-overall">
          <div class="pills-group" id="axis-pills-group">
            <span class="pills-label">X-Axis:</span>
            <button class="pill active" data-axis="wall_median_s" type="button">Median Wall (s)</button>
            <button class="pill" data-axis="wall_mean_s" type="button">Mean Wall (s)</button>
            <button class="pill" data-axis="wall_total_min" type="button">Suite Time (min)</button>
            <button class="pill" data-axis="gen_tok_median" type="button">Median Tokens</button>
            <button class="pill" data-axis="pp_tps_median" type="button">Prefill (pp t/s)</button>
            <button class="pill" data-axis="tg_tps_median" type="button">Decode (tg t/s)</button>
          </div>
          <div class="pills-group" id="lbl-pills-group">
            <span class="pills-label">Labels:</span>
            <button class="pill active" data-lbl="hover" type="button" title="Clean dots; hover or click any point to reveal/pin its label">Hover / Click</button>
            <button class="pill" data-lbl="frontier" type="button" title="Label Pareto frontier points only">Frontier</button>
            <button class="pill" data-lbl="all" type="button">All</button>
          </div>
        </div>

        <div class="pills-bar" id="controls-category" style="display: none;">
          <div class="pills-group" id="cat-pills-group"></div>
          <div class="pills-group" id="cat-lbl-pills-group">
            <span class="pills-label">Labels:</span>
            <button class="pill active" data-lbl="hover" type="button">Hover / Click</button>
            <button class="pill" data-lbl="frontier" type="button">Frontier</button>
            <button class="pill" data-lbl="all" type="button">All</button>
          </div>
        </div>

        <div class="chart-stage" id="chart-stage">
          <div class="zoom-corner" id="zoom-corner">
            <button class="zoom-reset-btn mono" id="reset-zoom-btn" type="button" title="Click or double-click chart to reset zoom">
              <span id="zoom-level-text">1.0×</span> · Reset Zoom
            </button>
          </div>
          <svg id="pareto-svg" viewBox="0 0 880 500" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pareto Frontier Plot"></svg>
        </div>

        <div id="matrix-stage" class="table-wrap" style="display: none; border-top: none;"></div>

        <div class="chart-footer" id="chart-footer">
          <div class="legend-row" id="chart-legend"></div>
        </div>

        <div class="table-wrap" id="scoreboard-wrap"></div>
      </section>

      <aside class="dock">
        <div class="card">
          <div class="card-header">
            <div class="dock-title">
              <span>Expand &amp; Compare Dock</span>
              <span class="status-pill" id="runs-count-pill">0 Runs</span>
            </div>
          </div>
          <div class="dock-body dock-grid">
            <div style="display: flex; flex-direction: column; gap: 11px;">
              <div style="font-size: 11.5px; color: var(--text-secondary);">
                Colors denote model family (<span style="color:#ef4444;font-weight:700">Red = QFN</span>, <span style="color:#38bdf8">Blue = Inkling</span>, <span style="color:#a78bfa">Violet = Qwen3.8-27B</span>, <span style="color:#fb923c">Orange = Qwen3.6-35B</span>, <span style="color:#fbbf24">Gold = Qwen3.5-122B</span>, <span style="color:#34d399">Green = Gemma 4</span>, <span style="color:#e2e8f0">Silver = LFM</span>, <span style="color:#eeff00">Neon Yellow = Phi-4</span>, <span style="color:#f472b6">Pink = Qwen3/3.5 Small</span>). Hover any dot for telemetry; click to pin label.
              </div>
              <div class="field">
                <label for="json-file-input">Import Run JSON File(s)</label>
                <input id="json-file-input" type="file" accept=".json,application/json" multiple />
              </div>
              <div class="custom-list" id="added-list"></div>
            </div>

            <div class="form-section" style="border-top: none; padding-top: 0;">
              <div class="form-heading">
                <span>+ Plot Quick Manual Point</span>
                <button id="clear-custom-btn" class="btn btn-ghost-sm" type="button">Clear Added</button>
              </div>
              <form id="manual-form" novalidate>
                <div class="form-grid">
                  <div class="field full">
                    <label for="m-label">Run Label</label>
                    <input id="m-label" type="text" placeholder="e.g. Qwen3.6-35B Fat (Rig B)" required />
                  </div>
                  <div class="field">
                    <label for="m-host">System / Host</label>
                    <input id="m-host" type="text" value="Custom" />
                  </div>
                  <div class="field">
                    <label for="m-acc">Pass@1 (%)</label>
                    <input id="m-acc" type="number" step="0.1" min="0" max="100" placeholder="93.8" required />
                  </div>
                  <div class="field">
                    <label for="m-wall">Med Wall (s)</label>
                    <input id="m-wall" type="number" step="0.1" min="0.1" placeholder="25.0" required />
                  </div>
                  <div class="field">
                    <label for="m-tok">Med Tokens</label>
                    <input id="m-tok" type="number" step="1" min="1" placeholder="650" />
                  </div>
                  <div class="field">
                    <label for="m-pp">Prefill (t/s)</label>
                    <input id="m-pp" type="number" step="0.1" min="0.1" placeholder="450" />
                  </div>
                  <div class="field">
                    <label for="m-tg">Decode (t/s)</label>
                    <input id="m-tg" type="number" step="0.1" min="0.1" placeholder="35.0" />
                  </div>
                </div>
                <div class="form-msg" id="m-form-msg"></div>
                <button class="btn btn-emerald" type="submit" style="width: 100%; margin-top: 2px;">Add to Comparison</button>
              </form>
            </div>
          </div>
        </div>
      </aside>
    </main>
  </div>

  <div id="tooltip" class="tooltip"></div>
  <div id="toast" class="toast"></div>

  <script>
    (function () {
      'use strict';

      const INITIAL_RUNS = __RUNS_JSON__;
      const FAMILY_META = {
        inkling: { label: 'Inkling-2-Mini',     color: '#38bdf8' },
        qwflash: { label: 'QFN',                color: '#ef4444' },
        qw35:    { label: 'Qwen3.5-122B-A10B',  color: '#fbbf24' },
        qw38:    { label: 'Qwen3.8-27B',        color: '#a78bfa' },
        qw36:    { label: 'Qwen3.6-35B-A3B',    color: '#fb923c' },
        gemma:   { label: 'Gemma 4',            color: '#34d399' },
        lfm:     { label: 'LFM 2.5 / 3',        color: '#e2e8f0' },
        phi:     { label: 'Phi-4 Mini',         color: '#eeff00' },
        qw3:     { label: 'Qwen3 / 3.5 Small',  color: '#f472b6' },
        other:   { label: 'Other',              color: '#64748b' },
      };

      const AXIS_CONFIG = {
        wall_median_s:  { key: 'wall_median_s',  label: 'Median Wall Time / Task (seconds)', shortLabel: 'Median Wall (s)', unit: 's',    lowerIsBetter: true,  better: '↖ Better · Faster & Higher',        worse: 'Slower & Lower · Worse ↘' },
        wall_mean_s:    { key: 'wall_mean_s',    label: 'Mean (Average) Wall Time / Task (seconds)', shortLabel: 'Mean Wall (s)', unit: 's', lowerIsBetter: true, better: '↖ Better · Faster Avg & Higher', worse: 'Slower Avg & Lower · Worse ↘' },
        wall_total_min: { key: 'wall_total_min', label: 'Total Suite Wall Time (minutes)',   shortLabel: 'Suite Time (min)', unit: 'm',   lowerIsBetter: true,  better: '↖ Better · Shorter & Higher',       worse: 'Longer & Lower · Worse ↘' },
        gen_tok_median: { key: 'gen_tok_median', label: 'Median Output Tokens / Task',       shortLabel: 'Median Tokens',    unit: ' tok',lowerIsBetter: true,  better: '↖ Better · Concise & Higher',       worse: 'Verbose & Lower · Worse ↘' },
        pp_tps_median:  { key: 'pp_tps_median',  label: 'Median Prefill Speed (tokens/s)',   shortLabel: 'Prefill (pp t/s)', unit: ' t/s', lowerIsBetter: false, better: 'Faster Prefill & Higher · Better ↗', worse: '↙ Worse · Slower Prefill & Lower' },
        tg_tps_median:  { key: 'tg_tps_median',  label: 'Median Decode Speed (tokens/s)',    shortLabel: 'Decode (tg t/s)',  unit: ' t/s', lowerIsBetter: false, better: 'Faster Decode & Higher · Better ↗',  worse: '↙ Worse · Slower Decode & Lower' },
      };

      function meanWall(s) {
        if (!s) return 0;
        if (typeof s.wall_mean_s === 'number' && !isNaN(s.wall_mean_s) && s.wall_mean_s > 0) return s.wall_mean_s;
        return Number(((s.wall_total_s || 0) / Math.max(1, s.total_tasks || 1)).toFixed(2));
      }

      const CAT_META = {
        coding:            { label: 'Twisted Coding',     short: 'Coding' },
        reasoning:         { label: 'Symbolic Reasoning', short: 'Symbolic' },
        structured:        { label: 'Strict Schema',      short: 'Schema' },
        long_context:      { label: 'Long Context',       short: 'Long Ctx' },
        agent_tools:       { label: 'Agent Tools',        short: 'Tools' },
        ops_and_voice:     { label: 'Ops & Voice',        short: 'Ops/Voice' },
        frontier_ceiling:  { label: 'Frontier Ceiling',   short: 'Frontier' },
        epistemic_honesty: { label: 'Epistemic Honesty',  short: 'Honesty' },
        surgical_diff:     { label: 'Surgical Diff',      short: 'Diff' },
        tool_recovery:     { label: 'Tool Recovery',      short: 'Recovery' },
        two_turn_recovery: { label: '2-Turn Recovery',    short: 'Recovery' },
        agent_harness:     { label: 'Agent Harness',      short: 'Agentic' },
        frontier_agentic:  { label: 'Frontier Agentic',   short: 'Front-Ag' },
      };

      const V1_TASK_IDS = new Set([
        'code_vm_salt','code_sched_penalty','code_log_compact','code_bug_hunt',
        'reason_state_chain','reason_graph_constraints','reason_rule_eval','reason_base_checksum',
        'struct_json_reconcile','struct_tool_dispatch','struct_table_extract','struct_negative_constraints',
        'ctx_4k_multihop','ctx_4k_diff_audit','ctx_16k_state_mutation','ctx_16k_needle_contradiction',
      ]);

      const FRONTIER_TASK_IDS = new Set([
        'front_bilevel_dp','front_nonlocal_bijection_10','front_16k_rollback_audit','front_ood_affine_tx_taint',
      ]);

      const AGENTIC_11_IDS = new Set([
        'agent_read_patch_verify','agent_probe_honesty','agent_antiloop_pivot','agent_foreign_injection',
        'agent_contract_drift','agent_clean_handoff','agent_rapidocal_fraction','agent_lsmtop_journal_metrics',
        'agent_oxirack_ccol_bounds','agent_armyman_flank_combat','agent_impossible_p_vs_np',
      ]);

      const AGENTIC_FRONTIER_IDS = new Set([
        'agent_hard_aliased_rollback','agent_hard_stale_doc_trap','agent_hard_mock_shadowing_bug','agent_hard_partial_pipeline_stall',
      ]);

      const SUITE_GROUPS = [
        { id: 'v3',               label: 'v3 (28 Tasks)',      desc: 'Full 28-task parity suite — identical prompt set across every model & rig' },
        { id: 'v1',               label: 'v1 Core (16 Tasks)', desc: 'Core 16 tasks — native 16-task runs + matching 16-task slice from v3 runs' },
        { id: 'frontier',         label: 'Frontier (4 Tasks)', desc: '4 hardest frontier_ceiling tasks — standalone subsets + matching 4-task slice from v3' },
        { id: 'extended_v4',      label: 'Extended v4 (6t)',   desc: 'Epistemic honesty, minimal unified diff & 2-turn self-correction recovery' },
        { id: 'agentic',          label: 'Agentic (11t)',      desc: 'Multi-turn sandboxed repo tasks (11 baseline tasks — slices cleanly from 15t runs)' },
        { id: 'agentic_frontier', label: 'Agentic Frontier (4t)', desc: '4 multi-file/misleading-test hard agentic tasks (tier=frontier style)' },
        { id: 'agentic_full',     label: 'Agentic Full (15t)', desc: 'All 15 sandboxed agentic tasks combined (11 baseline + 4 frontier_agentic)' },
        { id: 'combined',         label: 'All Suites (Avg)',   desc: 'Cross-suite macro-average per model config across independent suites completed' },
      ];

      function detectInitialGroup(runs) {
        if (runs.some(r => r.suite === 'v3' && (r.subset || 'all') === 'all')) return 'v3';
        if (runs.some(r => r.suite === 'extended_v4')) return 'extended_v4';
        if (runs.some(r => r.suite === 'agentic')) return 'agentic';
        if (runs.some(r => (r.subset || 'all') === 'all')) return 'v1';
        return 'combined';
      }

      const state = {
        runs: INITIAL_RUNS,
        disabledIds: new Set(),
        pinnedIds: new Set(),
        drawerOpen: false,
        systemFilter: 'all',
        suiteGroup: detectInitialGroup(INITIAL_RUNS),
        mode: 'overall',
        axis: 'wall_median_s',
        labelMode: 'hover',
        catFilter: 'all',
        showTainted: false,
        addedIds: [],
        zoom: { x0: 0, x1: 1, y0: 0, y1: 1 },
        suppressClickUntil: 0,
      };

      const svgEl = document.getElementById('pareto-svg');
      const tooltipEl = document.getElementById('tooltip');
      const toastEl = document.getElementById('toast');

      const W = 880, H = 500, PAD = { top: 20, right: 48, bottom: 56, left: 68 };
      const PLOT_W = W - PAD.left - PAD.right, PLOT_H = H - PAD.top - PAD.bottom, Y_MAX = 114;

      function el(tag, attrs = {}, text = null) {
        const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
        if (text !== null) n.textContent = text;
        return n;
      }

      // esc() neutralizes untrusted fields before DOM template insertion
      function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
      }

      function shortRigTag(h) {
        if (!h) return '';
        if (h.includes('Rig A')) return 'A';
        if (h.includes('Rig B')) return 'B';
        return h.slice(0, 6);
      }

      function shortModelBase(name) {
        const s = String(name || '');
        const l = s.toLowerCase();
        if (l.includes('flash-next')) return 'QFN';
        if (l.includes('inkling')) return 'Inkling';
        if (l.includes('3.8-27b')) return 'Qw3.8-27B';
        if (l.includes('3.6-35b') || l.includes('3.6-35b')) return 'Qw3.6-35B';
        if (l.includes('3.5-122b')) return 'Qw3.5-122B';
        if (l.includes('gemma-4-26b')) return 'Gemma4-26B';
        if (l.includes('gemma-4-12b')) return 'Gemma4-12B';
        if (l.includes('gemma-4-e4b')) return 'Gemma4-E4B';
        if (l.includes('gemma-4-e2b')) return 'Gemma4-E2B';
        if (l.includes('qwen3-4b')) return 'Qw3-4B';
        if (l.includes('lfm2.5')) return 'LFM2.5';
        if (l.includes('lfm3')) return 'LFM3-2B';
        if (l.includes('phi-4')) return 'Phi4-Mini';
        return s.length > 16 ? s.slice(0, 15) + '…' : s;
      }

      function shortQuantTag(q) {
        const s = String(q || '').toUpperCase();
        if (!s) return '';
        if (s.includes('IQ3')) return 'IQ3';
        if (s.includes('Q4_K')) return 'Q4';
        if (s.includes('Q5_K')) return 'Q5';
        if (s.includes('Q6_K')) return 'Q6';
        if (s.includes('Q2_0')) return 'Q2';
        if (s.includes('Q4_0')) return 'QAT';
        if (s.includes('BF16')) return 'BF16';
        if (s.startsWith('IT')) return 'IT';
        return s.split(/[_ +]/)[0].slice(0, 6);
      }

      function shortEffortTag(eff) {
        if (!eff || eff === 'default' || eff === 'think') return '';
        if (eff === 'medium') return 'med';
        return eff;
      }

      function shortRunName(r) {
        const base = shortModelBase(r.model);
        const q = shortQuantTag(r.quant);
        const e = shortEffortTag(r.effort);
        const cap = (r.max_tokens >= 16384 && !(r.effort || '').includes('16k')) ? '16k' : '';
        const sub = r._sliceTag === 'subset' ? 'sub' : '';
        return [base, q, e, cap, sub].filter(Boolean).join(' ');
      }

      function runDisplayName(r) {
        if (r._combinedLabel) return r._combinedLabel;
        const parts = [r.model, r.quant];
        if (r.draft_model) parts.push('+ Draft');
        const capTag = (r.max_tokens >= 16384 && !(r.effort || '').includes('16k')) ? '16k' : '';
        const effStr = [r.effort && r.effort !== 'default' ? r.effort : '', capTag].filter(Boolean).join(' ');
        if (effStr) parts.push(`(${effStr})`);
        if (r._sliceTag) parts.push(`[${r._sliceTag}]`);
        return parts.filter(Boolean).join(' ');
      }

      function medianNum(arr) {
        if (!arr.length) return 0;
        const s = [...arr].sort((a, b) => a - b);
        const m = Math.floor(s.length / 2);
        return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
      }

      function projectTaskSubset(r, idSet, targetSuite, sliceTag) {
        if (!r.tasks || !r.tasks.length) return r;
        const sub = r.tasks.filter(t => idSet.has(t.id));
        if (!sub.length) return null;
        if (sub.length === r.tasks.length && r.suite === targetSuite) return r;
        const walls = sub.map(t => t.wall_s || 0);
        const sortedW = [...walls].sort((a, b) => a - b);
        const p90 = sortedW[Math.max(0, Math.min(sub.length - 1, Math.round(0.9 * (sub.length - 1))))];
        const passed = sub.filter(t => t.passed).length;
        const dAcc = sub.reduce((a, t) => a + (t.draft_n_accepted || 0), 0);
        const dTot = sub.reduce((a, t) => a + (t.draft_n || 0), 0);
        const cats = {};
        const catNames = Array.from(new Set(sub.map(t => t.category).filter(Boolean)));
        catNames.forEach(ck => {
          const cr = sub.filter(t => t.category === ck);
          if (cr.length) {
            cats[ck] = {
              pass_count: cr.filter(t => t.passed).length,
              total: cr.length,
              pass_pct: Number((100 * cr.filter(t => t.passed).length / cr.length).toFixed(1)),
              wall_median_s: Number(medianNum(cr.map(t => t.wall_s || 0)).toFixed(2)),
            };
          }
        });
        const pps = sub.map(t => t.pp_tps).filter(v => typeof v === 'number' && v > 0);
        const tgs = sub.map(t => t.tg_tps).filter(v => typeof v === 'number' && v > 0);
        return {
          ...r,
          suite: targetSuite,
          subset: 'all',
          _sliceTag: sliceTag,
          tasks: sub,
          categories: cats,
          summary: {
            pass_count: passed,
            total_tasks: sub.length,
            pass_pct: Number((100 * passed / sub.length).toFixed(1)),
            truncated: sub.filter(t => t.finish_reason === 'length').length,
            wall_total_s: Number(walls.reduce((a, b) => a + b, 0).toFixed(2)),
            wall_total_min: Number((walls.reduce((a, b) => a + b, 0) / 60).toFixed(2)),
            wall_median_s: Number(medianNum(walls).toFixed(2)),
            wall_mean_s: Number((walls.reduce((a, b) => a + b, 0) / Math.max(1, sub.length)).toFixed(2)),
            wall_p90_s: Number(p90.toFixed(2)),
            gen_tok_total: sub.reduce((a, t) => a + (t.predicted_n || 0), 0),
            gen_tok_median: Math.round(medianNum(sub.map(t => t.predicted_n || 0))),
            pp_tps_median: pps.length ? Number(medianNum(pps).toFixed(1)) : null,
            tg_tps_median: tgs.length ? Number(medianNum(tgs).toFixed(1)) : null,
            draft_accepted: dAcc,
            draft_total: dTot,
            draft_accept_pct: dTot ? Number((100 * dAcc / dTot).toFixed(1)) : null,
          },
        };
      }

      function isTaintedRun(r) {
        return Boolean(r.run_validity && r.run_validity.status === 'invalidated');
      }

      function runsInGroup(groupId, respectDisabled = true, respectSystem = true) {
        const raw = state.runs.filter(r => {
          if (!state.showTainted && isTaintedRun(r)) return false;
          if (respectSystem && state.systemFilter !== 'all' && r.host_label !== state.systemFilter) return false;
          if (respectDisabled && state.disabledIds.has(r.run_id)) return false;
          return true;
        });
        if (groupId === 'v3') {
          return raw.filter(r => r.suite === 'v3' && (r.subset || 'all') === 'all');
        }
        if (groupId === 'v1') {
          return raw
            .filter(r => (r.suite === 'v1' || r.suite === 'v2' || r.suite === 'v3') && (r.subset || 'all') === 'all')
            .map(r => (r.suite === 'v1' ? r : projectTaskSubset(r, V1_TASK_IDS, 'v1', `from ${r.suite}`)))
            .filter(Boolean);
        }
        if (groupId === 'frontier') {
          return raw
            .filter(r => (r.subset === 'frontier' && r.suite !== 'agentic') || (r.suite === 'v3' && (r.subset || 'all') === 'all'))
            .map(r => (r.subset === 'frontier' ? { ...r, _sliceTag: 'subset' } : projectTaskSubset(r, FRONTIER_TASK_IDS, 'frontier', 'from v3')))
            .filter(Boolean);
        }
        if (groupId === 'extended_v4') {
          return raw.filter(r => r.suite === 'extended_v4');
        }
        if (groupId === 'agentic') {
          return raw
            .filter(r => r.suite === 'agentic' && (r.subset || 'all') === 'all')
            .map(r => ((r.summary && r.summary.total_tasks === 11) ? r : projectTaskSubset(r, AGENTIC_11_IDS, 'agentic', '11t slice')))
            .filter(Boolean);
        }
        if (groupId === 'agentic_frontier') {
          return raw
            .filter(r => r.suite === 'agentic' && (r.subset === 'frontier_agentic' || (r.tasks || []).some(t => AGENTIC_FRONTIER_IDS.has(t.id))))
            .map(r => (r.subset === 'frontier_agentic' ? { ...r, _sliceTag: 'subset' } : projectTaskSubset(r, AGENTIC_FRONTIER_IDS, 'agentic_frontier', 'from 15t')))
            .filter(Boolean);
        }
        if (groupId === 'agentic_full') {
          return raw.filter(r => r.suite === 'agentic' && (r.subset || 'all') === 'all' && r.summary && r.summary.total_tasks >= 15);
        }
        if (groupId === 'combined') {
          return raw.filter(r => (r.subset || 'all') === 'all');
        }
        return raw;
      }

      function buildCombinedRuns(rawRuns) {
        const buckets = new Map();
        rawRuns.forEach(r => {
          if ((r.subset || 'all') !== 'all') return;
          const suite = r.suite || 'v1';
          if (!['v3', 'v2', 'v1', 'extended_v4', 'agentic'].includes(suite)) return;
          const hasDraft = Boolean(r.draft_model);
          const key = [r.model || '', r.quant || '', r.effort || 'default', hasDraft ? 'draft' : 'nodraft', r.host_label || ''].join('|');
          if (!buckets.has(key)) buckets.set(key, { proto: r, bySuite: {} });
          const entry = buckets.get(key);
          const prev = entry.bySuite[suite];
          if (!prev || (r.summary && prev.summary && r.summary.pass_pct > prev.summary.pass_pct)) {
            entry.bySuite[suite] = r;
          }
        });
        const combined = [];
        buckets.forEach((entry, key) => {
          const r0 = entry.proto;
          const sMap = entry.bySuite;
          // Prefer full v3 over v1/v2 so the shared 16 core tasks are never double-counted
          const pickedSuites = [];
          if (sMap.v3) pickedSuites.push(['v3', sMap.v3]);
          else if (sMap.v2) pickedSuites.push(['v2', sMap.v2]);
          else if (sMap.v1) pickedSuites.push(['v1', sMap.v1]);
          if (sMap.extended_v4) pickedSuites.push(['extended_v4', sMap.extended_v4]);
          if (sMap.agentic) pickedSuites.push(['agentic', sMap.agentic]);
          if (!pickedSuites.length) return;
          const avg = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
          const passPcts = pickedSuites.map(([, x]) => x.summary.pass_pct || 0);
          const wallsMed = pickedSuites.map(([, x]) => x.summary.wall_median_s || 0).filter(v => v > 0);
          const wallsMean = pickedSuites.map(([, x]) => meanWall(x.summary)).filter(v => v > 0);
          const pps = pickedSuites.map(([, x]) => x.summary.pp_tps_median).filter(v => typeof v === 'number' && v > 0);
          const tgs = pickedSuites.map(([, x]) => x.summary.tg_tps_median).filter(v => typeof v === 'number' && v > 0);
          const toks = pickedSuites.map(([, x]) => x.summary.gen_tok_median || 0).filter(v => v > 0);
          const totalPass = pickedSuites.reduce((a, [, x]) => a + (x.summary.pass_count || 0), 0);
          const totalTasks = pickedSuites.reduce((a, [, x]) => a + (x.summary.total_tasks || 0), 0);
          const totalTrunc = pickedSuites.reduce((a, [, x]) => a + (x.summary.truncated || 0), 0);
          const totalWallS = pickedSuites.reduce((a, [, x]) => a + (x.summary.wall_total_s || 0), 0);
          const totalTok = pickedSuites.reduce((a, [, x]) => a + (x.summary.gen_tok_total || 0), 0);
          const lblParts = [r0.model, r0.quant, r0.draft_model ? '+ Draft' : null, (r0.effort && r0.effort !== 'default') ? `(${r0.effort})` : null].filter(Boolean);
          const cats = {};
          pickedSuites.forEach(([, sr]) => {
            Object.entries(sr.categories || {}).forEach(([ck, cv]) => { cats[ck] = cv; });
          });
          combined.push({
            ...r0,
            run_id: `combined::${key}`,
            _combinedLabel: lblParts.join(' '),
            _suitesCount: pickedSuites.length,
            _suiteScores: sMap,
            _constituentIds: pickedSuites.map(([, x]) => x.run_id),
            categories: cats,
            summary: {
              pass_count: totalPass,
              total_tasks: totalTasks,
              pass_pct: Number(avg(passPcts).toFixed(1)),
              truncated: totalTrunc,
              wall_total_s: Number(totalWallS.toFixed(2)),
              wall_total_min: Number((totalWallS / 60).toFixed(2)),
              wall_median_s: Number(avg(wallsMed).toFixed(2)),
              wall_mean_s: Number(avg(wallsMean).toFixed(2)),
              wall_p90_s: null,
              gen_tok_total: totalTok,
              gen_tok_median: Math.round(avg(toks)),
              pp_tps_median: pps.length ? Number(avg(pps).toFixed(1)) : null,
              tg_tps_median: tgs.length ? Number(avg(tgs).toFixed(1)) : null,
              draft_accepted: 0,
              draft_total: 0,
              draft_accept_pct: null,
            },
          });
        });
        return combined.sort((a, b) => (b._suitesCount - a._suitesCount) || (b.summary.pass_pct - a.summary.pass_pct));
      }

      function visibleRuns() {
        const list = runsInGroup(state.suiteGroup, true, true);
        if (state.suiteGroup === 'combined') return buildCombinedRuns(list);
        return list;
      }

      function computeFrontier(points, xKey, yKey, lowerIsBetter) {
        const valid = points.filter(p => typeof p[xKey] === 'number' && !isNaN(p[xKey]) && p[xKey] > 0);
        const frontRaw = valid.filter(a => !valid.some(b => {
          if (a === b) return false;
          const bx = lowerIsBetter ? b[xKey] <= a[xKey] : b[xKey] >= a[xKey];
          const by = b[yKey] >= a[yKey];
          const strict = lowerIsBetter ? (b[xKey] < a[xKey] || b[yKey] > a[yKey]) : (b[xKey] > a[xKey] || b[yKey] > a[yKey]);
          return bx && by && strict;
        }));
        frontRaw.sort((a, b) => a[xKey] !== b[xKey] ? a[xKey] - b[xKey] : (lowerIsBetter ? a[yKey] - b[yKey] : b[yKey] - a[yKey]));
        // Keep a single representative at identical (x, y) coordinates so labels & spline never duplicate
        const seenXY = new Set();
        const front = [];
        for (const p of frontRaw) {
          const k = `${Number(p[xKey]).toFixed(4)}|${Number(p[yKey]).toFixed(4)}`;
          if (seenXY.has(k)) continue;
          seenXY.add(k);
          front.push(p);
        }
        return front;
      }

      function getNiceScale(maxVal) {
        if (maxVal <= 0) return { max: 10, ticks: [0, 2, 4, 6, 8, 10] };
        const rough = maxVal / 5, mag = Math.pow(10, Math.floor(Math.log10(rough))), norm = rough / mag;
        const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
        const niceMax = Math.ceil(maxVal / step) * step;
        const ticks = [];
        for (let v = 0; v <= niceMax + step * 0.001; v += step) ticks.push(Number(v.toFixed(2)));
        return { max: niceMax, ticks };
      }

      function getRangeTicks(lo, hi, target = 6) {
        const span = Math.max(1e-6, hi - lo);
        const rough = span / target, mag = Math.pow(10, Math.floor(Math.log10(rough))), norm = rough / mag;
        const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
        const start = Math.ceil((lo - step * 1e-6) / step) * step;
        const ticks = [];
        for (let v = start; v <= hi + step * 1e-6; v += step) ticks.push(Number(v.toFixed(4)));
        return ticks;
      }

      function isZoomed() {
        return (state.zoom.x1 - state.zoom.x0) < 0.995 || (state.zoom.y1 - state.zoom.y0) < 0.995;
      }

      function curDomain(baseXMax) {
        return {
          xMin: state.zoom.x0 * baseXMax,
          xMax: state.zoom.x1 * baseXMax,
          yMin: state.zoom.y0 * Y_MAX,
          yMax: state.zoom.y1 * Y_MAX,
        };
      }

      function syncZoomUi() {
        const zCorner = document.getElementById('zoom-corner');
        const zoomed = isZoomed();
        zCorner.classList.toggle('visible', zoomed && state.mode !== 'matrix');
        svgEl.classList.toggle('zoomed', zoomed);
        if (zoomed) {
          const factor = Math.max(1 / Math.max(0.02, state.zoom.x1 - state.zoom.x0), 1 / Math.max(0.02, state.zoom.y1 - state.zoom.y0));
          document.getElementById('zoom-level-text').textContent = `${factor.toFixed(1)}×`;
        }
      }

      function resetZoom() {
        state.zoom = { x0: 0, x1: 1, y0: 0, y1: 1 };
        syncZoomUi();
      }

      const sx = (v, dom) => PAD.left + ((v - dom.xMin) / Math.max(1e-9, dom.xMax - dom.xMin)) * PLOT_W;
      const sy = (p, dom) => PAD.top + PLOT_H - ((p - dom.yMin) / Math.max(1e-9, dom.yMax - dom.yMin)) * PLOT_H;

      function renderBaseGrid(dom, xLabel, xUnit, hints, lowerIsBetter) {
        svgEl.innerHTML = '';
        const defs = el('defs');
        const fGrad = el('linearGradient', { id: 'frontier-area-grad', x1: 0, y1: 0, x2: 0, y2: 1 });
        fGrad.appendChild(el('stop', { offset: '0%', 'stop-color': '#38bdf8', 'stop-opacity': '0.16' }));
        fGrad.appendChild(el('stop', { offset: '100%', 'stop-color': '#38bdf8', 'stop-opacity': '0.0' }));
        defs.appendChild(fGrad);
        const clip = el('clipPath', { id: 'plot-clip' });
        clip.appendChild(el('rect', { x: PAD.left, y: PAD.top, width: PLOT_W, height: PLOT_H }));
        defs.appendChild(clip);
        svgEl.appendChild(defs);
        svgEl.appendChild(el('rect', { x: 0, y: 0, width: W, height: H, fill: '#0f172a', rx: 10 }));
        svgEl.appendChild(el('style', {}, '.lbl-halo { paint-order: stroke; stroke: #0b1120; stroke-width: 4.5px; stroke-linejoin: round; pointer-events: none; }'));
        svgEl.appendChild(el('rect', { x: PAD.left, y: PAD.top, width: PLOT_W, height: PLOT_H, fill: '#0b1120', stroke: '#1f2937', 'stroke-width': 1 }));

        const yTicks = isZoomed() ? getRangeTicks(dom.yMin, Math.min(100, dom.yMax), 5).filter(v => v >= 0 && v <= 105) : [0, 25, 50, 75, 100];
        if (dom.yMax >= 100 && dom.yMin <= 100 && !yTicks.some(v => Math.abs(v - 100) < 0.01)) yTicks.push(100);
        yTicks.forEach(pct => {
          const y = sy(pct, dom);
          if (y < PAD.top - 1 || y > PAD.top + PLOT_H + 1) return;
          const is100 = Math.abs(pct - 100) < 0.01;
          svgEl.appendChild(el('line', { x1: PAD.left, y1: y, x2: PAD.left + PLOT_W, y2: y, stroke: is100 ? '#334155' : '#1f2937', 'stroke-dasharray': is100 ? '4 4' : 'none' }));
          const lbl = Number.isInteger(pct) ? `${pct}%` : `${pct.toFixed(1)}%`;
          svgEl.appendChild(el('text', { x: PAD.left - 12, y: y + 4, fill: is100 ? '#cbd5e1' : '#64748b', 'font-size': 11.5, 'font-family': 'ui-monospace, monospace', 'text-anchor': 'end' }, lbl));
        });

        const xTicks = getRangeTicks(dom.xMin, dom.xMax, 6);
        xTicks.forEach(val => {
          const x = sx(val, dom);
          if (x < PAD.left - 1 || x > PAD.left + PLOT_W + 1) return;
          svgEl.appendChild(el('line', { x1: x, y1: PAD.top, x2: x, y2: PAD.top + PLOT_H, stroke: '#1f2937' }));
          const fmt = val >= 1000 ? `${(val / 1000).toFixed(val % 1000 === 0 ? 0 : 1)}k` : (Number.isInteger(val) ? `${val}` : `${Number(val.toFixed(2))}`);
          svgEl.appendChild(el('text', { x, y: PAD.top + PLOT_H + 20, fill: '#64748b', 'font-size': 11.5, 'font-family': 'ui-monospace, monospace', 'text-anchor': 'middle' }, `${fmt}${xUnit === ' tok' ? '' : xUnit}`));
        });

        svgEl.appendChild(el('text', { x: PAD.left + PLOT_W / 2, y: H - 12, fill: '#94a3b8', 'font-size': 12.5, 'font-weight': 600, 'text-anchor': 'middle' }, xLabel));
        svgEl.appendChild(el('text', { x: 20, y: PAD.top + PLOT_H / 2, fill: '#94a3b8', 'font-size': 12.5, 'font-weight': 600, 'text-anchor': 'middle', transform: `rotate(-90, 20, ${PAD.top + PLOT_H / 2})` }, 'Pass@1 Accuracy (%)'));

        const betterText = (hints && hints.better) || (lowerIsBetter ? '↖ Better' : 'Better ↗');
        const worseText = (hints && hints.worse) || (lowerIsBetter ? 'Worse ↘' : '↙ Worse');
        svgEl.appendChild(el('text', {
          x: lowerIsBetter ? PAD.left + 12 : PAD.left + PLOT_W - 12,
          y: PAD.top + 18,
          fill: 'rgba(56, 189, 248, 0.62)',
          'font-size': 11, 'font-weight': 600, 'letter-spacing': '0.03em',
          'text-anchor': lowerIsBetter ? 'start' : 'end',
          'pointer-events': 'none',
        }, betterText));
        svgEl.appendChild(el('text', {
          x: lowerIsBetter ? PAD.left + PLOT_W - 12 : PAD.left + 12,
          y: PAD.top + PLOT_H - 12,
          fill: 'rgba(100, 116, 139, 0.52)',
          'font-size': 10.5, 'font-weight': 600, 'letter-spacing': '0.03em',
          'text-anchor': lowerIsBetter ? 'end' : 'start',
          'pointer-events': 'none',
        }, worseText));

        const plotLayer = el('g', { id: 'plot-layer', 'clip-path': 'url(#plot-clip)' });
        svgEl.appendChild(plotLayer);
        syncZoomUi();
        return plotLayer;
      }

      function monotoneSegments(pts) {
        const n = pts.length;
        if (n < 2) return [];
        const dx = [], m = [];
        for (let i = 0; i < n - 1; i++) {
          const dxi = Math.max(0.5, pts[i + 1].x - pts[i].x);
          dx.push(dxi);
          m.push((pts[i + 1].y - pts[i].y) / dxi);
        }
        const tang = new Array(n).fill(0);
        tang[0] = m[0];
        tang[n - 1] = m[n - 2];
        for (let i = 1; i < n - 1; i++) {
          if (m[i - 1] * m[i] <= 0) {
            tang[i] = 0;
          } else {
            const w1 = 2 * dx[i] + dx[i - 1], w2 = dx[i] + 2 * dx[i - 1];
            tang[i] = (w1 + w2) / (w1 / m[i - 1] + w2 / m[i]);
          }
        }
        const segs = [];
        for (let i = 0; i < n - 1; i++) {
          const h = dx[i] / 3;
          const c1x = pts[i].x + h, c1y = pts[i].y + tang[i] * h;
          const c2x = pts[i + 1].x - h, c2y = pts[i + 1].y - tang[i + 1] * h;
          segs.push({
            x0: pts[i].x, y0: pts[i].y,
            cmd: `C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${pts[i + 1].x.toFixed(2)} ${pts[i + 1].y.toFixed(2)}`,
          });
        }
        return segs;
      }

      function renderFrontierPath(layer, front, xKey, yKey, dom, lowerIsBetter) {
        if (!front.length) return;
        const pts = front.map(p => ({ x: sx(p[xKey], dom), y: sy(p[yKey], dom) })).sort((a, b) => a.x - b.x);
        const y0Px = sy(0, dom), y100Px = sy(100, dom);
        const leftEdge = PAD.left, rightEdge = PAD.left + PLOT_W, plotBottom = PAD.top + PLOT_H;
        const leftAnchor = lowerIsBetter
          ? { x: Math.min(pts[0].x - 0.5, Math.max(leftEdge, sx(0, dom))), y: y0Px }
          : { x: Math.min(pts[0].x - 0.5, leftEdge), y: y100Px };
        const rightAnchor = lowerIsBetter
          ? { x: Math.max(pts[pts.length - 1].x + 0.5, rightEdge), y: y100Px }
          : { x: Math.max(pts[pts.length - 1].x + 0.5, rightEdge), y: y0Px };

        const extPts = [];
        if (pts[0].x > leftAnchor.x + 1 || Math.abs(pts[0].y - leftAnchor.y) > 1) extPts.push(leftAnchor);
        const coreStart = extPts.length;
        pts.forEach(p => extPts.push(p));
        const hasRight = (rightAnchor.x > pts[pts.length - 1].x + 1 || Math.abs(rightAnchor.y - pts[pts.length - 1].y) > 1);
        if (hasRight) extPts.push(rightAnchor);

        const segs = monotoneSegments(extPts);
        if (!segs.length) return;
        const fullD = `M ${extPts[0].x.toFixed(2)} ${extPts[0].y.toFixed(2)} ` + segs.map(s => s.cmd).join(' ');
        const areaD = `${fullD} L ${extPts[extPts.length - 1].x.toFixed(2)} ${plotBottom} L ${extPts[0].x.toFixed(2)} ${plotBottom} Z`;
        layer.appendChild(el('path', { d: areaD, fill: 'url(#frontier-area-grad)' }));

        // Projected tails (dashed, softer) + measured core frontier (solid) sharing identical C1 spline tangents
        const coreEndSeg = coreStart + Math.max(0, pts.length - 1);
        if (coreStart > 0) {
          const dLeft = `M ${segs[0].x0.toFixed(2)} ${segs[0].y0.toFixed(2)} ${segs[0].cmd}`;
          layer.appendChild(el('path', { d: dLeft, fill: 'none', stroke: '#38bdf8', 'stroke-width': 2.0, 'stroke-dasharray': '5 5', 'stroke-linecap': 'round', 'stroke-opacity': 0.58 }));
        }
        if (pts.length >= 2) {
          const coreSegs = segs.slice(coreStart, coreEndSeg);
          const dCore = `M ${coreSegs[0].x0.toFixed(2)} ${coreSegs[0].y0.toFixed(2)} ` + coreSegs.map(s => s.cmd).join(' ');
          layer.appendChild(el('path', { d: dCore, fill: 'none', stroke: '#38bdf8', 'stroke-width': 2.5, 'stroke-linecap': 'round', 'stroke-opacity': 0.92 }));
        }
        if (hasRight) {
          const rSeg = segs[segs.length - 1];
          const dRight = `M ${rSeg.x0.toFixed(2)} ${rSeg.y0.toFixed(2)} ${rSeg.cmd}`;
          layer.appendChild(el('path', { d: dRight, fill: 'none', stroke: '#38bdf8', 'stroke-width': 2.0, 'stroke-dasharray': '5 5', 'stroke-linecap': 'round', 'stroke-opacity': 0.58 }));
        }
      }

      function drawMarker(layer, cx, cy, color, shape, isFront, isHighlighted, payload, kind, ptId, labelGroup) {
        const g = el('g', { class: 'svg-point ' + (isHighlighted || isFront ? 'active' : 'dimmed') });
        if (isFront || isHighlighted) {
          g.appendChild(el('circle', { cx, cy, r: isHighlighted ? 14 : 12, fill: color, 'fill-opacity': isHighlighted ? 0.22 : 0.13, stroke: color, 'stroke-opacity': 0.6 }));
        }
        if (shape === 'diamond') {
          const s = 7.5;
          g.appendChild(el('polygon', { points: `${cx},${cy - s} ${cx + s},${cy} ${cx},${cy + s} ${cx - s},${cy}`, fill: color, stroke: '#0b1120', 'stroke-width': 2 }));
        } else if (shape === 'hex') {
          const r = 8.2, pts = [];
          for (let i = 0; i < 6; i++) { const a = (Math.PI / 3) * i - Math.PI / 6; pts.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`); }
          g.appendChild(el('polygon', { points: pts.join(' '), fill: color, stroke: '#0b1120', 'stroke-width': 2 }));
        } else {
          g.appendChild(el('circle', { cx, cy, r: 7.2, fill: color, stroke: '#0b1120', 'stroke-width': 2 }));
        }
        g.appendChild(el('circle', { cx, cy, r: 17, fill: 'transparent' }));
        g.addEventListener('mouseenter', e => {
          g.classList.remove('dimmed');
          g.classList.add('active');
          if (labelGroup) labelGroup.style.display = 'inline';
          showTooltip(e, payload, kind);
        });
        g.addEventListener('mousemove', moveTooltip);
        g.addEventListener('mouseleave', () => {
          if (!isHighlighted && !isFront) g.classList.add('dimmed');
          g.classList.remove('active');
          if (labelGroup && labelGroup.dataset.keep !== '1') labelGroup.style.display = 'none';
          hideTooltip();
        });
        g.addEventListener('click', () => {
          if (Date.now() < state.suppressClickUntil) return;
          if (state.pinnedIds.has(ptId)) state.pinnedIds.delete(ptId);
          else state.pinnedIds.add(ptId);
          renderAll();
        });
        layer.appendChild(g);
        return g;
      }

      function renderInteractiveLegend(items, kind) {
        const leg = document.getElementById('chart-legend');
        leg.innerHTML = '';
        items.forEach(it => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'legend-item legend-btn' + (state.pinnedIds.has(it.id) ? ' pinned' : '');
          b.setAttribute('aria-label', runDisplayName(it.run));
          const sw = it.shape === 'diamond'
            ? `<span class="legend-diamond" style="background:${it.color}"></span>`
            : `<span class="legend-swatch" style="background:${it.color}"></span>`;
          const rig = shortRigTag(it.run.host_label);
          const rigSpan = rig ? `<span class="legend-rig">(${esc(rig)})</span>` : '';
          b.innerHTML = `${sw}<span>${esc(shortRunName(it.run))}</span>${rigSpan}`;
          const enter = e => {
            if (it.marker) { it.marker.classList.remove('dimmed'); it.marker.classList.add('active'); }
            if (it.labelGroup) it.labelGroup.style.display = 'inline';
            showTooltip(e, it.payload, kind);
          };
          const leave = () => {
            if (it.marker && !it.isFront && !state.pinnedIds.has(it.id)) {
              it.marker.classList.add('dimmed');
              it.marker.classList.remove('active');
            }
            if (it.labelGroup && it.labelGroup.dataset.keep !== '1') it.labelGroup.style.display = 'none';
            hideTooltip();
          };
          b.addEventListener('mouseenter', enter);
          b.addEventListener('mousemove', moveTooltip);
          b.addEventListener('mouseleave', leave);
          b.addEventListener('focus', enter);
          b.addEventListener('blur', leave);
          b.addEventListener('click', () => {
            if (state.pinnedIds.has(it.id)) state.pinnedIds.delete(it.id);
            else state.pinnedIds.add(it.id);
            renderAll();
          });
          leg.appendChild(b);
        });
        const curveItem = document.createElement('span');
        curveItem.className = 'legend-item';
        curveItem.innerHTML = '<span class="legend-dash"></span><span>Pareto Curve</span>';
        leg.appendChild(curveItem);
      }

      function renderFiltersAndKpis() {
        const mkPill = (val, label, activeVal, onClick) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'pill' + (activeVal === val ? ' active' : '');
          b.textContent = label;
          b.onclick = onClick;
          return b;
        };

        const sfSuite = document.getElementById('suite-filters');
        sfSuite.innerHTML = '<span class="pills-label">Suite:</span>';
        const availGroups = SUITE_GROUPS.filter(g => g.id === 'combined' ? (new Set(state.runs.filter(r => state.showTainted || !isTaintedRun(r)).map(r => r.suite || 'v1')).size >= 2) : runsInGroup(g.id, false, false).length > 0);
        if (availGroups.length && !availGroups.some(g => g.id === state.suiteGroup)) {
          state.suiteGroup = availGroups[0].id;
        }
        availGroups.forEach(g => {
          const cnt = g.id === 'combined' ? buildCombinedRuns(runsInGroup('combined', false, true)).length : runsInGroup(g.id, false, true).length;
          const lbl = g.id === 'combined' ? `${g.label} (${cnt})` : `${g.label} · ${cnt}`;
          const b = mkPill(g.id, lbl, state.suiteGroup, () => {
            state.suiteGroup = g.id;
            state.catFilter = 'all';
            resetZoom();
            renderAll();
          });
          b.title = g.desc;
          sfSuite.appendChild(b);
        });
        const curMeta = SUITE_GROUPS.find(g => g.id === state.suiteGroup) || SUITE_GROUPS[0];
        document.getElementById('suite-explain').textContent = curMeta.desc;

        const hosts = Array.from(new Set(state.runs.map(r => r.host_label)));
        const sf = document.getElementById('system-filters');
        sf.innerHTML = '<span class="pills-label">Rig:</span>';
        sf.appendChild(mkPill('all', 'All Rigs', state.systemFilter, () => { state.systemFilter = 'all'; resetZoom(); renderAll(); }));
        hosts.forEach(h => sf.appendChild(mkPill(h, h, state.systemFilter, () => { state.systemFilter = h; resetZoom(); renderAll(); })));

        const taintedCount = state.runs.filter(isTaintedRun).length;
        if (taintedCount > 0) {
          const tb = document.createElement('button');
          tb.type = 'button';
          tb.className = 'pill' + (state.showTainted ? ' active' : '');
          tb.style.marginLeft = '8px';
          tb.textContent = `Show Tainted (${taintedCount})`;
          tb.title = 'Include runs quarantined by perf_floor or hardware bottleneck checks';
          tb.onclick = () => { state.showTainted = !state.showTainted; renderAll(); };
          sf.appendChild(tb);
        }

        const groupScopeAll = runsInGroup(state.suiteGroup, false, true);
        const groupActive = runsInGroup(state.suiteGroup, true, true);
        const mq = document.getElementById('model-quick-filters');
        mq.innerHTML = '<span class="pills-label">Models:</span>';
        const fams = Array.from(new Set(groupScopeAll.map(r => r.family_key || 'other')));
        fams.forEach(fk => {
          const famRuns = groupScopeAll.filter(r => (r.family_key || 'other') === fk);
          const activeCnt = famRuns.filter(r => !state.disabledIds.has(r.run_id)).length;
          const anyActive = activeCnt > 0;
          const meta = FAMILY_META[fk] || { label: fk, color: '#94a3b8' };
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'pill' + (anyActive ? ' active' : '');
          b.style.borderColor = anyActive ? meta.color : '';
          b.style.color = anyActive ? meta.color : '';
          b.textContent = `${meta.label} (${activeCnt}/${famRuns.length})`;
          b.title = `Toggle ${meta.label} runs in ${curMeta.label}`;
          b.onclick = () => {
            if (anyActive) famRuns.forEach(r => state.disabledIds.add(r.run_id));
            else famRuns.forEach(r => state.disabledIds.delete(r.run_id));
            renderAll();
          };
          mq.appendChild(b);
        });
        if (groupScopeAll.some(r => state.disabledIds.has(r.run_id))) {
          const rb = document.createElement('button');
          rb.type = 'button';
          rb.className = 'btn btn-ghost-sm';
          rb.textContent = 'Enable All';
          rb.onclick = () => { groupScopeAll.forEach(r => state.disabledIds.delete(r.run_id)); renderAll(); };
          mq.appendChild(rb);
        }

        document.getElementById('drawer-summary-text').textContent = `Runs (${groupActive.length}/${groupScopeAll.length})`;
        document.getElementById('drawer-hint-text').textContent = state.drawerOpen ? 'Hide ▴' : 'Toggle individual runs ▾';
        document.getElementById('runs-count-pill').textContent = `${groupActive.length} / ${groupScopeAll.length} Active`;

        const drawer = document.getElementById('runs-drawer');
        drawer.classList.toggle('collapsed', !state.drawerOpen);
        drawer.innerHTML = '';
        if (!groupScopeAll.length) {
          drawer.innerHTML = '<div style="font-size:12px;color:var(--text-muted)">No runs in this Suite + Rig selection.</div>';
        }
        fams.forEach(fk => {
          const famRuns = groupScopeAll.filter(r => (r.family_key || 'other') === fk);
          if (!famRuns.length) return;
          const meta = FAMILY_META[fk] || { label: fk, color: '#94a3b8' };
          const grp = document.createElement('div');
          grp.className = 'model-group';
          grp.innerHTML = `<div class="model-group-head"><span class="model-group-title"><span class="legend-swatch" style="background:${meta.color}"></span>${esc(meta.label)}</span><span class="group-actions"><button class="btn btn-ghost-sm act-all" type="button">All</button><button class="btn btn-ghost-sm act-none" type="button">None</button><button class="btn btn-ghost-sm act-only" type="button">Only</button></span></div><div class="model-chips"></div>`;
          grp.querySelector('.act-all').onclick = () => { famRuns.forEach(r => state.disabledIds.delete(r.run_id)); renderAll(); };
          grp.querySelector('.act-none').onclick = () => { famRuns.forEach(r => state.disabledIds.add(r.run_id)); renderAll(); };
          grp.querySelector('.act-only').onclick = () => {
            groupScopeAll.forEach(r => state.disabledIds.add(r.run_id));
            famRuns.forEach(r => state.disabledIds.delete(r.run_id));
            renderAll();
          };
          const chips = grp.querySelector('.model-chips');
          famRuns.forEach(r => {
            const active = !state.disabledIds.has(r.run_id);
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'run-chip' + (active ? ' active' : '');
            b.style.setProperty('--chip-color', r.color);
            const suiteTag = state.suiteGroup === 'combined' ? (r.suite || 'v1') : (r._sliceTag || null);
            const tag = [r.quant, r.draft_model ? '+Draft' : null, r.effort !== 'default' ? r.effort : null, suiteTag].filter(Boolean).join(' · ');
            b.innerHTML = `<span class="run-chip-left"><span class="run-chip-dot"></span><span>${esc(tag || r.model)} <span style="color:var(--text-muted)">(${esc(r.host_label)})</span></span></span><span class="run-chip-meta mono">${r.summary.pass_pct.toFixed(0)}% · ${r.summary.wall_median_s}s</span>`;
            b.onclick = () => {
              if (active) state.disabledIds.add(r.run_id);
              else state.disabledIds.delete(r.run_id);
              renderAll();
            };
            chips.appendChild(b);
          });
          drawer.appendChild(grp);
        });

        const plotted = visibleRuns();
        const kpi = document.getElementById('kpi-strip');
        if (!plotted.length) {
          kpi.innerHTML = '<div class="kpi-card" style="grid-column:1/-1"><span class="kpi-label">No matching runs</span><span class="kpi-sub">Select another Suite, switch Rig to All Rigs, or re-enable models above.</span></div>';
          return;
        }
        const bestAcc = [...plotted].sort((a, b) => b.summary.pass_pct - a.summary.pass_pct || a.summary.wall_median_s - b.summary.wall_median_s)[0];
        const fastestWall = [...plotted].sort((a, b) => a.summary.wall_median_s - b.summary.wall_median_s)[0];
        const lowestTok = [...plotted].sort((a, b) => (a.summary.gen_tok_median || Infinity) - (b.summary.gen_tok_median || Infinity))[0];
        const validTg = plotted.filter(r => typeof r.summary.tg_tps_median === 'number' && r.summary.tg_tps_median > 0);
        const fastestTg = (validTg.length ? validTg : plotted).sort((a, b) => (b.summary.tg_tps_median || 0) - (a.summary.tg_tps_median || 0))[0];
        const accLabel = state.suiteGroup === 'combined' ? 'Highest Macro-Avg Pass@1' : 'Highest Pass@1';
        const accSub = state.suiteGroup === 'combined' ? `${bestAcc._suitesCount} suites · ${bestAcc.summary.pass_count}/${bestAcc.summary.total_tasks} tasks` : `${bestAcc.summary.pass_count}/${bestAcc.summary.total_tasks}`;
        kpi.innerHTML = `
          <div class="kpi-card"><span class="kpi-label">${accLabel}</span><span class="kpi-value mono" style="color:${bestAcc.color}">${bestAcc.summary.pass_pct.toFixed(1)}% <span style="font-size:13px;font-weight:500;color:var(--text-secondary)">(${esc(accSub)})</span></span><span class="kpi-sub">${esc(runDisplayName(bestAcc))} · ${esc(bestAcc.host_label)}</span></div>
          <div class="kpi-card"><span class="kpi-label">Fastest Median Wall</span><span class="kpi-value mono" style="color:${fastestWall.color}">${fastestWall.summary.wall_median_s}s</span><span class="kpi-sub">${esc(runDisplayName(fastestWall))} · ${fastestWall.summary.wall_total_min}m total</span></div>
          <div class="kpi-card"><span class="kpi-label">Lowest Median Tokens</span><span class="kpi-value mono" style="color:${lowestTok.color}">${(lowestTok.summary.gen_tok_median || 0).toLocaleString()} tok</span><span class="kpi-sub">${esc(runDisplayName(lowestTok))} · ${lowestTok.summary.gen_tok_total.toLocaleString()} total</span></div>
          <div class="kpi-card"><span class="kpi-label">Peak Median Decode</span><span class="kpi-value mono" style="color:${fastestTg.color}">${fastestTg.summary.tg_tps_median || '—'} t/s</span><span class="kpi-sub">${esc(runDisplayName(fastestTg))} · pp ${fastestTg.summary.pp_tps_median || '—'} t/s</span></div>
        `;
      }

      function placeSmartLabel(cx, cy, w, h, preferUp, labelBoxes, dotBoxes) {
        const minX = PAD.left + 6, maxX = PAD.left + PLOT_W - 6;
        const minY = PAD.top + 4, maxY = PAD.top + PLOT_H - 4;
        const overlapArea = (bx0, by0, bx1, by1) => {
          let area = 0;
          for (const q of labelBoxes) {
            const ox = Math.max(0, Math.min(bx1 + 6, q.x1) - Math.max(bx0 - 6, q.x0));
            const oy = Math.max(0, Math.min(by1 + 4, q.y1) - Math.max(by0 - 4, q.y0));
            area += ox * oy;
          }
          for (const d of dotBoxes) {
            const ox = Math.max(0, Math.min(bx1, d.x1) - Math.max(bx0, d.x0));
            const oy = Math.max(0, Math.min(by1, d.y1) - Math.max(by0, d.y0));
            area += ox * oy * 4;
          }
          return area;
        };
        const mk = (anchor, dx, dy) => {
          const lx = anchor === 'end' ? cx - dx : cx + dx;
          const ly = cy + dy;
          const bx0 = anchor === 'end' ? lx - w : lx;
          const bx1 = anchor === 'end' ? lx : lx + w;
          return { anchor, lx, ly, bx0, by0: ly - 11, bx1, by1: ly + h - 11 };
        };
        const cands = [];
        if (preferUp) {
          cands.push(mk('start', 14, -24), mk('end', 14, -24), mk('start', 14, 16), mk('end', 14, 16));
          for (let step = 1; step <= 8; step++) {
            const dyU = -24 - step * (h + 6);
            const dyD = 16 + step * (h + 6);
            cands.push(mk('start', 14, dyU), mk('end', 14, dyU), mk('start', 14 + step * 18, dyU), mk('end', 14 + step * 18, dyU));
            cands.push(mk('start', 14, dyD), mk('end', 14, dyD), mk('start', 14 + step * 18, dyD), mk('end', 14 + step * 18, dyD));
          }
        } else {
          cands.push(mk('start', 14, 18), mk('end', 14, 18));
          for (let step = 1; step <= 8; step++) {
            const dyD = 18 + step * (h + 6);
            const dyU = -24 - step * (h + 6);
            cands.push(mk('start', 14, dyD), mk('end', 14, dyD), mk('start', 14 + step * 18, dyD), mk('end', 14 + step * 18, dyD));
            cands.push(mk('start', 14, dyU), mk('end', 14, dyU));
          }
        }
        let bestInBounds = null, bestArea = Infinity;
        for (const c of cands) {
          if (c.bx0 < minX || c.bx1 > maxX || c.by0 < minY || c.by1 > maxY) continue;
          const oa = overlapArea(c.bx0, c.by0, c.bx1, c.by1);
          if (oa === 0) return c;
          if (oa < bestArea) { bestArea = oa; bestInBounds = c; }
        }
        if (bestInBounds) return bestInBounds;
        const anchor = (cx + 14 + w <= maxX) ? 'start' : 'end';
        const lx = anchor === 'end' ? Math.max(minX + w, Math.min(maxX, cx - 14)) : Math.min(maxX - w, Math.max(minX, cx + 14));
        const ly = Math.max(minY + 12, Math.min(maxY - h + 11, preferUp ? cy - 24 : cy + 18));
        return { anchor, lx, ly, bx0: anchor === 'end' ? lx - w : lx, by0: ly - 11, bx1: anchor === 'end' ? lx : lx + w, by1: ly + h - 11 };
      }

      function renderOverall() {
        const allPlotted = visibleRuns();
        const axisValue = (r, key) => key === 'wall_mean_s' ? meanWall(r.summary) : (r.summary[key] || 0);
        document.querySelectorAll('#axis-pills-group .pill[data-axis]').forEach(b => {
          const axKey = b.dataset.axis;
          const hasAny = allPlotted.some(r => axisValue(r, axKey) > 0);
          b.style.display = hasAny || !allPlotted.length ? '' : 'none';
          b.classList.toggle('active', axKey === state.axis);
        });
        if (allPlotted.length && !allPlotted.some(r => axisValue(r, state.axis) > 0)) {
          state.axis = 'wall_median_s';
          document.querySelectorAll('#axis-pills-group .pill[data-axis]').forEach(b => b.classList.toggle('active', b.dataset.axis === state.axis));
        }
        const cfg = AXIS_CONFIG[state.axis];
        const pts = allPlotted.map(r => ({
          run: r,
          id: r.run_id,
          color: r.color,
          shape: r.shape || 'circle',
          passPct: r.summary.pass_pct,
          xVal: axisValue(r, cfg.key),
        })).filter(p => p.xVal > 0);

        const yDesc = state.suiteGroup === 'combined' ? 'Macro-Avg Pass@1 (%)' : 'Pass@1 (%)';
        document.getElementById('chart-context-tag').textContent = `Y: ${yDesc}  •  X: ${cfg.shortLabel}  •  Scroll/pinch zoom, drag pan, click dot to pin`;
        const maxVal = pts.length ? Math.max(...pts.map(p => p.xVal)) : 100;
        const { max: baseXMax } = getNiceScale(maxVal * 1.15);
        const dom = curDomain(baseXMax);
        const layer = renderBaseGrid(dom, cfg.label, cfg.unit, cfg, cfg.lowerIsBetter);

        if (!pts.length) {
          layer.appendChild(el('text', { x: PAD.left + PLOT_W / 2, y: PAD.top + PLOT_H / 2, fill: '#64748b', 'font-size': 13, 'text-anchor': 'middle' }, 'No runs match the current Suite, Rig & Model filters.'));
          document.getElementById('chart-legend').innerHTML = '';
          renderScoreboard([], new Set());
          return;
        }

        const front = computeFrontier(pts, 'xVal', 'passPct', cfg.lowerIsBetter);
        const frontIds = new Set(front.map(p => p.id));
        renderFrontierPath(layer, front, 'xVal', 'passPct', dom, cfg.lowerIsBetter);

        const sortedPts = [...pts].sort((a, b) => {
          const fa = frontIds.has(a.id) ? 0 : 1, fb = frontIds.has(b.id) ? 0 : 1;
          if (fa !== fb) return fa - fb;
          if (b.passPct !== a.passPct) return b.passPct - a.passPct;
          return cfg.lowerIsBetter ? (a.xVal - b.xVal) : (b.xVal - a.xVal);
        });
        const dotBoxes = sortedPts.map(p => {
          const cx = sx(p.xVal, dom), cy = sy(p.passPct, dom);
          return { x0: cx - 9, x1: cx + 9, y0: cy - 9, y1: cy + 9 };
        });
        const labelBoxes = [];
        const labelGroups = [];
        const maxPass = sortedPts.length ? Math.max(...sortedPts.map(p => p.passPct)) : 100;
        sortedPts.forEach((p, idx) => {
          const cx = sx(p.xVal, dom), cy = sy(p.passPct, dom);
          const isFront = frontIds.has(p.id);
          const isPinned = state.pinnedIds.has(p.id);
          const inView = cx >= PAD.left - 8 && cx <= PAD.left + PLOT_W + 8 && cy >= PAD.top - 8 && cy <= PAD.top + PLOT_H + 8;
          const showStatic = inView && (isPinned || state.labelMode === 'all' || (state.labelMode === 'frontier' && isFront));

          const lg = el('g');
          lg.style.display = showStatic ? 'inline' : 'none';
          lg.dataset.keep = showStatic ? '1' : '0';

          const title = runDisplayName(p.run);
          const valStr = cfg.key === 'gen_tok_median' ? `${p.xVal.toLocaleString()} tok` : `${p.xVal}${cfg.unit}`;
          const subExtra = state.suiteGroup === 'combined' ? ` (${p.run._suitesCount}s)` : '';
          const sub = `${p.passPct.toFixed(1)}%${subExtra} @ ${valStr} · ${p.run.host_label}`;
          const w = Math.max(title.length * 7.2, sub.length * 6.7) + 10;
          const preferUp = isFront || p.passPct >= maxPass - 2.0 || idx === 0;
          const box = placeSmartLabel(cx, cy, w, 28, preferUp, labelBoxes, dotBoxes);
          if (showStatic) labelBoxes.push({ x0: box.bx0, x1: box.bx1, y0: box.by0, y1: box.by1 });
          if (Math.hypot(box.lx - cx, box.ly - cy) > 36) {
            lg.appendChild(el('line', { x1: cx, y1: cy, x2: box.lx, y2: box.ly + 2, stroke: p.color, 'stroke-width': 1, 'stroke-opacity': 0.45, 'stroke-dasharray': '2 2' }));
          }
          lg.appendChild(el('text', { class: 'lbl-halo', x: box.lx, y: box.ly, fill: '#f1f5f9', 'font-size': 11.5, 'font-weight': 700, 'text-anchor': box.anchor }, title));
          lg.appendChild(el('text', { class: 'lbl-halo', x: box.lx, y: box.ly + 14, fill: p.color, 'font-size': 10.5, 'font-weight': 600, 'font-family': 'ui-monospace, monospace', 'text-anchor': box.anchor }, sub));
          labelGroups.push(lg);
        const markerRef = drawMarker(layer, cx, cy, p.color, p.shape, isFront, isPinned, { ...p.run, _isFront: isFront }, 'overall', p.id, lg);
        p._marker = markerRef;
        p._labelGroup = lg;
      });
      labelGroups.forEach(lg => layer.appendChild(lg));

      renderInteractiveLegend(pts.map(p => ({
        run: p.run, id: p.id, color: p.color, shape: p.shape, isFront: frontIds.has(p.id),
        marker: p._marker, labelGroup: p._labelGroup, payload: { ...p.run, _isFront: frontIds.has(p.id) },
      })), 'overall');

      renderScoreboard(allPlotted, frontIds);
    }

      function renderCategory() {
        const runs = visibleRuns();
        const availCats = Array.from(new Set(runs.flatMap(r => Object.keys(r.categories || {}))));
        if (state.catFilter !== 'all' && !availCats.includes(state.catFilter)) state.catFilter = 'all';

        const catBar = document.getElementById('cat-pills-group');
        catBar.innerHTML = '<span class="pills-label">Category:</span>';
        const mkCatPill = (val, label) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'pill' + (state.catFilter === val ? ' active' : '');
          b.textContent = label;
          b.onclick = () => { state.catFilter = val; renderAll(); };
          return b;
        };
        catBar.appendChild(mkCatPill('all', 'All Categories'));
        availCats.forEach(ck => catBar.appendChild(mkCatPill(ck, (CAT_META[ck] && CAT_META[ck].label) || ck)));

        const cats = state.catFilter === 'all' ? availCats : [state.catFilter];
        const pts = [];
        runs.forEach(r => {
          cats.forEach(ck => {
            const c = r.categories && r.categories[ck];
            if (!c) return;
            pts.push({
              id: `${r.run_id}:${ck}`,
              run: r, cat: ck, passPct: c.pass_pct, passCount: c.pass_count, total: c.total, medianWall: c.wall_median_s,
              color: r.color, shape: r.shape || 'circle',
            });
          });
        });

        document.getElementById('chart-context-tag').textContent = 'Y: Category Pass@1 (%)  •  X: Median Wall (s)  •  Scroll/pinch zoom, drag pan';
        const maxVal = pts.length ? Math.max(...pts.map(p => p.medianWall)) : 180;
        const { max: baseXMax } = getNiceScale(maxVal * 1.15);
        const dom = curDomain(baseXMax);
        const layer = renderBaseGrid(dom, 'Category Median Wall Time (seconds)', 's', { better: '↖ Better · Faster & Higher', worse: 'Slower & Lower · Worse ↘' }, true);

        if (!pts.length) {
          layer.appendChild(el('text', { x: PAD.left + PLOT_W / 2, y: PAD.top + PLOT_H / 2, fill: '#64748b', 'font-size': 13, 'text-anchor': 'middle' }, 'No category breakdown available for the current filter.'));
          document.getElementById('chart-legend').innerHTML = '';
          renderScoreboard(runs, new Set());
          return;
        }

        const front = computeFrontier(pts, 'medianWall', 'passPct', true);
        const frontIds = new Set(front.map(p => p.id));
        renderFrontierPath(layer, front, 'medianWall', 'passPct', dom, true);

        const sortedCatPts = [...pts].sort((a, b) => {
          const fa = frontIds.has(a.id) ? 0 : 1, fb = frontIds.has(b.id) ? 0 : 1;
          if (fa !== fb) return fa - fb;
          return b.passPct - a.passPct || a.medianWall - b.medianWall;
        });
        const dotBoxes = sortedCatPts.map(p => {
          const cx = sx(p.medianWall, dom), cy = sy(p.passPct, dom);
          return { x0: cx - 8, x1: cx + 8, y0: cy - 8, y1: cy + 8 };
        });
        const labelBoxes = [];
        const labelGroups = [];
        sortedCatPts.forEach((p, idx) => {
          const cx = sx(p.medianWall, dom), cy = sy(p.passPct, dom);
          const isFront = frontIds.has(p.id), isPinned = state.pinnedIds.has(p.id);
          const inView = cx >= PAD.left - 8 && cx <= PAD.left + PLOT_W + 8 && cy >= PAD.top - 8 && cy <= PAD.top + PLOT_H + 8;
          const showStatic = inView && (isPinned || state.labelMode === 'all' || (state.labelMode === 'frontier' && isFront));
          const lg = el('g');
          lg.style.display = showStatic ? 'inline' : 'none';
          lg.dataset.keep = showStatic ? '1' : '0';
          const cShort = (CAT_META[p.cat] && CAT_META[p.cat].short) || p.cat;
          const lbl = `${cShort} · ${runDisplayName(p.run)} (${p.medianWall}s)`;
          const box = placeSmartLabel(cx, cy, lbl.length * 6.7 + 8, 16, isFront || idx === 0, labelBoxes, dotBoxes);
          if (showStatic) labelBoxes.push({ x0: box.bx0, x1: box.bx1, y0: box.by0, y1: box.by1 });
          lg.appendChild(el('text', { class: 'lbl-halo', x: box.lx, y: box.ly, fill: p.color, 'font-size': 10.5, 'font-weight': 600, 'font-family': 'ui-monospace, monospace', 'text-anchor': box.anchor }, lbl));
          labelGroups.push(lg);
          drawMarker(layer, cx, cy, p.color, p.shape, isFront, isPinned, { ...p, _isFront: isFront }, 'category', p.id, lg);
        });
        labelGroups.forEach(lg => layer.appendChild(lg));

        renderInteractiveLegend(runs.map(r => ({
          run: r, id: r.run_id, color: r.color, shape: r.shape || 'circle', isFront: false,
          marker: null, labelGroup: null, payload: { ...r, _isFront: false },
        })), 'overall');

        renderScoreboard(runs, new Set());
      }

      function renderMatrix() {
        const stage = document.getElementById('matrix-stage');
        if (state.suiteGroup === 'combined') {
          stage.innerHTML = '<div style="padding:20px;color:var(--text-secondary)">Task Matrix compares individual prompts within one comparable Suite. Pick <strong>v3 (28 Tasks)</strong>, <strong>v1 Core</strong>, <strong>Frontier</strong>, <strong>Extended v4</strong>, or <strong>Agentic</strong> above to view per-task results.</div>';
          document.getElementById('chart-context-tag').textContent = 'Select a single Suite above for Task Matrix';
          return;
        }
        const runs = visibleRuns();
        if (!runs.length) { stage.innerHTML = '<div style="padding:18px;color:var(--text-secondary)">No runs match the active filters — try enabling runs or switching Rig/Suite above.</div>'; return; }
        const taskIds = Array.from(new Set(runs.flatMap(r => (r.tasks || []).map(t => t.id))));
        let htmlStr = '<table><thead><tr><th>Task</th><th>Category</th>' + runs.map(r => `<th><span style="color:${r.color}">${esc(runDisplayName(r))}</span><br><span style="font-weight:400;color:var(--text-muted)">${esc(r.host_label)}</span></th>`).join('') + '</tr></thead><tbody>';
        taskIds.forEach(tid => {
          const first = runs.map(r => (r.tasks || []).find(t => t.id === tid)).find(Boolean);
          htmlStr += `<tr><td class="mono">${esc(tid)}</td><td>${esc(first ? first.category : '')}</td>`;
          runs.forEach(r => {
            const t = (r.tasks || []).find(x => x.id === tid);
            if (!t) { htmlStr += '<td class="mono" style="color:var(--text-muted)">—</td>'; return; }
            const b = t.passed ? '<span class="badge-pass">PASS</span>' : (t.finish_reason === 'length' ? '<span class="badge-trunc">TRUNC</span>' : '<span class="badge-fail">FAIL</span>');
            const acc = t.draft_n ? ` · ${t.draft_n_accepted}/${t.draft_n}` : '';
            const sp = t.tg_tps != null ? ` @ ${t.tg_tps}t/s` : '';
            htmlStr += `<td class="mono">${b} ${t.wall_s.toFixed(1)}s <span style="color:var(--text-secondary)">(${t.predicted_n}t${sp}${acc})</span></td>`;
          });
          htmlStr += '</tr>';
        });
        htmlStr += '</tbody></table>';
        stage.innerHTML = htmlStr;
        document.getElementById('chart-context-tag').textContent = `${taskIds.length} Salted Tasks × ${runs.length} Runs`;
      }

      function buildRunInsight(r, isFront) {
        if (r.insight) return r.insight;
        const s = r.summary || {};
        const mw = meanWall(s);
        const cats = r.categories || {};
        const tasks = r.tasks || [];
        let s1 = '';
        if (r.subset && r.subset !== 'all') {
          s1 = `Subset ablation (${s.pass_count}/${s.total_tasks} in ${r.subset}, excluded from full-suite frontier).`;
        } else if (isFront) {
          s1 = `Pareto-optimal on this view (${s.pass_pct.toFixed(1)}% @ ${s.wall_median_s}s med / ${mw}s avg).`;
        } else {
          s1 = `Dominated on this view (${s.pass_pct.toFixed(1)}% @ ${s.wall_median_s}s med / ${mw}s avg).`;
        }

        const weakCats = Object.entries(cats)
          .filter(([, c]) => c.pass_pct < 100)
          .sort((a, b) => a[1].pass_pct - b[1].pass_pct)
          .map(([k, c]) => `${(CAT_META[k] && CAT_META[k].short) || k} ${c.pass_count}/${c.total}`);
        const truncByCat = {};
        tasks.forEach(t => { if (t.finish_reason === 'length') truncByCat[t.category] = (truncByCat[t.category] || 0) + 1; });
        const truncList = Object.entries(truncByCat).map(([k, n]) => `${(CAT_META[k] && CAT_META[k].short) || k} (${n})`);
        const fenceFails = tasks.filter(t => !t.passed && String(t.detail || '').includes('raw=False') && String(t.detail || '').includes('got={')).length;
        const draftBit = s.draft_total ? `${s.draft_accept_pct}% draft accept, ` : '';

        let s2 = '';
        if (s.truncated > 0) {
          s2 = `${s.truncated}/${s.total_tasks} tasks hit the ${r.max_tokens || 8192}-tok cap (${truncList.join(', ')}), inflating wall time (${s.gen_tok_median} med tok).`;
        } else if (r.effort === 'nothink' || (s.gen_tok_median < 120 && s.pass_pct < 90)) {
          s2 = `Non-thinking speed (${s.tg_tps_median} t/s, ${s.wall_total_min}m suite) trades off accuracy in ${weakCats.slice(0, 3).join(', ') || 'reasoning'}.`;
        } else if (s.pass_pct >= 99.9) {
          s2 = `Clean ${s.total_tasks}/${s.total_tasks} sweep (${draftBit}${s.tg_tps_median} t/s med decode, 0 truncations).`;
        } else if (fenceFails > 0) {
          s2 = `${s.tg_tps_median} t/s decode (${draftBit}${weakCats.slice(0, 2).join(', ')}); ${fenceFails} fail(s) wrapped valid JSON in markdown fences.`;
        } else if (weakCats.length) {
          s2 = `${draftBit}${s.tg_tps_median} t/s decode; misses concentrated in ${weakCats.slice(0, 3).join(', ')}.`;
        } else if (r.notes) {
          s2 = r.notes;
        }
        return s2 ? `${s1} ${s2}` : s1;
      }

      function renderScoreboard(runs, frontIds) {
        const wrap = document.getElementById('scoreboard-wrap');
        if (!runs.length) { wrap.innerHTML = ''; return; }
        if (state.suiteGroup === 'combined') {
          const fmtSuite = (sMap, key) => {
            const x = sMap && sMap[key];
            if (!x || !x.summary) return '<td class="mono" style="color:var(--text-muted)">—</td>';
            return `<td class="mono">${x.summary.pass_pct.toFixed(1)}% <span style="color:var(--text-muted)">(${x.summary.pass_count}/${x.summary.total_tasks})</span></td>`;
          };
          const rowsHtml = runs.map((r, idx) => {
            const s = r.summary;
            const badge = frontIds.has(r.run_id) ? '<span class="badge-win">Pareto</span>' : '';
            return `<tr data-sb-idx="${idx}" style="cursor:pointer;">
              <td><span class="legend-swatch" style="background:${r.color};margin-right:6px"></span><strong>${esc(runDisplayName(r))}</strong>${badge}<div style="font-size:11px;color:var(--text-muted)">${esc(r.host_label)}</div></td>
              <td class="mono">${r._suitesCount}</td>
              <td class="mono" style="color:${r.color};font-weight:700">${s.pass_pct.toFixed(1)}%</td>
              ${fmtSuite(r._suiteScores, 'v3')}
              ${fmtSuite(r._suiteScores, 'extended_v4')}
              ${fmtSuite(r._suiteScores, 'agentic')}
              ${fmtSuite(r._suiteScores, 'v1')}
              <td class="mono">${s.wall_median_s}s</td>
              <td class="mono">${s.tg_tps_median || '—'} / ${s.pp_tps_median || '—'} t/s</td>
            </tr>`;
          }).join('');
          wrap.innerHTML = `<table><thead><tr><th>Config / Rig</th><th>Suites</th><th>Macro-Avg</th><th>v3 (28t)</th><th>Ext-v4 (6t)</th><th>Agentic (11t)</th><th>v1 (16t)</th><th>Avg Med Wall</th><th>Decode / Prefill</th></tr></thead><tbody>${rowsHtml}</tbody></table>`;
        } else {
          const rowsHtml = runs.map((r, idx) => {
            const s = r.summary;
            const tBadge = isTaintedRun(r) ? '<span class="badge-trunc" style="margin-left:6px;background:rgba(239,68,68,0.22);color:#fca5a5;">TAINTED</span>' : '';
            const badge = frontIds.has(r.run_id) ? '<span class="badge-win">Pareto</span>' : '';
            const draftStr = s.draft_total ? ` (${s.draft_accept_pct}% draft)` : '';
            const mw = meanWall(s);
            const lv = r.llama_version && (r.llama_version.props_build_info || r.llama_version.label);
            const pwr = r.power_telemetry && r.power_telemetry.available ? `${r.power_telemetry.system_avg_w}W · ${r.power_telemetry.system_wh}Wh` : '';
            const subMeta = [r.host_label, r.backend, lv, pwr].filter(Boolean).join(' · ');
            return `<tr data-sb-idx="${idx}" style="cursor:pointer;">
              <td><span class="legend-swatch" style="background:${r.color};margin-right:6px"></span><strong>${esc(runDisplayName(r))}</strong>${badge}${tBadge}<div style="font-size:11px;color:var(--text-muted)">${esc(subMeta)}</div></td>
              <td class="mono" style="color:${r.color};font-weight:700">${s.pass_pct.toFixed(1)}% (${s.pass_count}/${s.total_tasks})</td>
              <td class="mono">${s.truncated}</td>
              <td class="mono">${s.wall_median_s}s / ${mw}s / ${s.wall_p90_s || '—'}s</td>
              <td class="mono">${s.wall_total_min}m (${s.wall_total_s}s)</td>
              <td class="mono">${s.gen_tok_total.toLocaleString()} (${s.gen_tok_median})</td>
              <td class="mono">${s.pp_tps_median ?? '—'} / ${s.tg_tps_median ?? '—'} t/s${draftStr}</td>
              <td class="mono">${esc(r.mem_label || '—')}</td>
            </tr>`;
          }).join('');
          wrap.innerHTML = `<table><thead><tr><th>Run / System</th><th>Pass@1</th><th>Trunc</th><th>Med / Mean / p90</th><th>Suite Time</th><th>Output Tok (Med)</th><th>Prefill / Decode</th><th>Memory</th></tr></thead><tbody>${rowsHtml}</tbody></table>`;
        }
        wrap.querySelectorAll('tbody tr[data-sb-idx]').forEach(tr => {
          const r = runs[Number(tr.dataset.sbIdx)];
          const payload = { ...r, _isFront: frontIds.has(r.run_id) };
          tr.addEventListener('mouseenter', e => showTooltip(e, payload, 'overall'));
          tr.addEventListener('mousemove', moveTooltip);
          tr.addEventListener('mouseleave', hideTooltip);
        });
      }

      function showTooltip(e, p, kind) {
        if (kind === 'overall') {
          const s = p.summary;
          const mw = meanWall(s);
          const summaryText = p._suiteScores
            ? `Macro-average across ${p._suitesCount} suite(s): ` + Object.entries(p._suiteScores).map(([k, v]) => `${k} ${v.summary.pass_pct.toFixed(1)}%`).join(' · ')
            : buildRunInsight(p, Boolean(p._isFront));
          tooltipEl.innerHTML = `
            <div class="tt-header"><span class="tt-title">${esc(runDisplayName(p))}</span><span class="tt-tag" style="background:rgba(148,163,184,0.16);color:${p.color}">${esc(p.host_label)}</span></div>
            <div class="tt-grid">
              <span class="tt-k">${p._suiteScores ? 'Macro-Avg Pass@1' : 'Pass@1'}</span><span class="tt-v" style="color:${p.color}">${s.pass_pct.toFixed(1)}% (${s.pass_count}/${s.total_tasks})</span>
              <span class="tt-k">Med / Mean / p90</span><span class="tt-v">${s.wall_median_s}s / ${mw}s / ${s.wall_p90_s || '—'}s</span>
              <span class="tt-k">Suite Time</span><span class="tt-v">${s.wall_total_min}m (${s.wall_total_s}s)</span>
              <span class="tt-k">Output Tokens</span><span class="tt-v">${s.gen_tok_total.toLocaleString()} (med ${s.gen_tok_median})</span>
              <span class="tt-k">Prefill / Decode</span><span class="tt-v">${s.pp_tps_median ?? '—'} / ${s.tg_tps_median ?? '—'} t/s</span>
              <span class="tt-k">Draft Accept</span><span class="tt-v">${s.draft_total ? s.draft_accept_pct + '% (' + s.draft_accepted + '/' + s.draft_total + ')' : '—'}</span>
              <span class="tt-k">Memory</span><span class="tt-v">${esc(p.mem_label || '—')}</span>
              <span class="tt-k">Build / Power</span><span class="tt-v">${esc((p.llama_version && (p.llama_version.props_build_info || p.llama_version.label)) || '—')} · ${p.power_telemetry && p.power_telemetry.available ? `${p.power_telemetry.system_avg_w}W (${p.power_telemetry.system_wh}Wh)` : '—'}</span>
              <span class="tt-k">Seed / Ctx</span><span class="tt-v">${p.seed} · ${p.ctx || '—'}</span>
            </div>
            <div class="tt-summary" style="border-left-color:${p.color}">${esc(summaryText)}</div>`;
        } else {
          const cLabel = (CAT_META[p.cat] && CAT_META[p.cat].label) || p.cat;
          const cObj = (p.run.categories && p.run.categories[p.cat]) || {};
          const catNote = p._isFront ? `Category Pareto frontier (${p.passPct}% @ ${p.medianWall}s).` : `${cLabel}: ${p.passCount}/${p.total} (${p.passPct}%) @ ${p.medianWall}s med (${cObj.gen_tok_median || '—'} med tok).`;
          tooltipEl.innerHTML = `
            <div class="tt-header"><span class="tt-title">${esc(runDisplayName(p.run))}</span><span class="tt-tag" style="background:rgba(148,163,184,0.16);color:${p.color}">${esc(cLabel)}</span></div>
            <div class="tt-grid">
              <span class="tt-k">System</span><span class="tt-v">${esc(p.run.host_label)}</span>
              <span class="tt-k">Category Pass@1</span><span class="tt-v" style="color:${p.color}">${p.passPct}% (${p.passCount}/${p.total})</span>
              <span class="tt-k">Median Wall</span><span class="tt-v">${p.medianWall}s</span>
            </div>
            <div class="tt-summary" style="border-left-color:${p.color}">${esc(catNote)}</div>`;
        }
        tooltipEl.classList.add('visible');
        moveTooltip(e);
      }

      function moveTooltip(e) {
        const pad = 16, r = tooltipEl.getBoundingClientRect();
        let cx = e && typeof e.clientX === 'number' && e.clientX > 0 ? e.clientX : 0;
        let cy = e && typeof e.clientY === 'number' && e.clientY > 0 ? e.clientY : 0;
        if (!cx && !cy && e && e.currentTarget && e.currentTarget.getBoundingClientRect) {
          const br = e.currentTarget.getBoundingClientRect();
          cx = br.left + br.width / 2;
          cy = Math.max(12, br.top - r.height - 12);
          tooltipEl.style.left = `${Math.max(8, Math.min(window.innerWidth - r.width - 10, cx - r.width / 2))}px`;
          tooltipEl.style.top = `${Math.max(8, cy)}px`;
          return;
        }
        let l = cx + pad, t = cy + pad;
        if (l + r.width > window.innerWidth - 10) l = cx - r.width - pad;
        if (t + r.height > window.innerHeight - 10) t = cy - r.height - pad;
        tooltipEl.style.left = `${Math.max(8, l)}px`;
        tooltipEl.style.top = `${Math.max(8, t)}px`;
      }
      function hideTooltip() { tooltipEl.classList.remove('visible'); }

      let toastTimer = null;
      function showToast(msg) {
        toastEl.textContent = msg;
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2600);
      }

      function renderAddedList() {
        const list = document.getElementById('added-list');
        list.innerHTML = '';
        state.addedIds.forEach(rid => {
          const r = state.runs.find(x => x.run_id === rid);
          if (!r) return;
          const div = document.createElement('div');
          div.className = 'custom-item';
          div.innerHTML = `<div><div style="font-weight:600;color:${r.color}">${esc(runDisplayName(r))}</div><div class="mono" style="font-size:11px;color:var(--text-secondary)">${r.summary.pass_pct}% · ${r.summary.wall_median_s}s · ${esc(r.host_label)}</div></div><button class="remove-btn" type="button">&times;</button>`;
          div.querySelector('.remove-btn').onclick = () => {
            state.runs = state.runs.filter(x => x.run_id !== rid);
            state.addedIds = state.addedIds.filter(x => x !== rid);
            renderAddedList();
            renderAll();
          };
          list.appendChild(div);
        });
      }

      function renderAll() {
        renderFiltersAndKpis();
        const isMatrix = state.mode === 'matrix';
        document.getElementById('chart-stage').style.display = isMatrix ? 'none' : 'block';
        document.getElementById('chart-footer').style.display = isMatrix ? 'none' : 'flex';
        document.getElementById('matrix-stage').style.display = isMatrix ? 'block' : 'none';
        document.getElementById('controls-overall').style.display = state.mode === 'overall' ? 'flex' : 'none';
        document.getElementById('controls-category').style.display = state.mode === 'category' ? 'flex' : 'none';
        if (state.mode === 'overall') renderOverall();
        else if (state.mode === 'category') renderCategory();
        else renderMatrix();
      }

      document.getElementById('drawer-toggle-btn').onclick = (e) => {
        state.drawerOpen = !state.drawerOpen;
        e.currentTarget.setAttribute('aria-expanded', state.drawerOpen ? 'true' : 'false');
        renderFiltersAndKpis();
      };

      document.getElementById('tab-overall').onclick = () => { state.mode = 'overall'; resetZoom(); setActiveTab('tab-overall'); renderAll(); };
      document.getElementById('tab-category').onclick = () => { state.mode = 'category'; resetZoom(); setActiveTab('tab-category'); renderAll(); };
      document.getElementById('tab-matrix').onclick = () => { state.mode = 'matrix'; resetZoom(); setActiveTab('tab-matrix'); renderAll(); };
      function setActiveTab(id) {
        ['tab-overall', 'tab-category', 'tab-matrix'].forEach(t => document.getElementById(t).classList.toggle('active', t === id));
      }

      document.querySelectorAll('#controls-overall .pill[data-axis]').forEach(b => {
        b.onclick = () => {
          document.querySelectorAll('#controls-overall .pill[data-axis]').forEach(x => x.classList.remove('active'));
          b.classList.add('active');
          state.axis = b.dataset.axis;
          resetZoom();
          renderAll();
        };
      });
      function syncLblPills() {
        document.querySelectorAll('#lbl-pills-group .pill[data-lbl], #cat-lbl-pills-group .pill[data-lbl]').forEach(x => {
          x.classList.toggle('active', x.dataset.lbl === state.labelMode);
        });
      }
      document.querySelectorAll('#lbl-pills-group .pill[data-lbl], #cat-lbl-pills-group .pill[data-lbl]').forEach(b => {
        b.onclick = () => {
          state.labelMode = b.dataset.lbl;
          syncLblPills();
          renderAll();
        };
      });

      document.getElementById('json-file-input').addEventListener('change', async (e) => {
        let added = 0;
        for (const file of e.target.files) {
          try {
            const parsed = JSON.parse(await file.text());
            const items = Array.isArray(parsed) ? parsed : [parsed];
            items.forEach(item => {
              if (!item) return;
              const okSchema = item.schema === 'salted-eval/v1' || (item.schema_version === 1 && (item.suite === 'extended_v4' || item.suite === 'agentic'));
              if (!okSchema) return;
              const existingIdx = state.runs.findIndex(r => r.run_id === item.run_id);
              const mlow = (item.model || '').toLowerCase();
              const fam = mlow.includes('inkling') ? 'inkling' : (mlow.includes('flash') ? 'qwflash' : (mlow.includes('122b') ? 'qw35' : ((mlow.includes('27b') || mlow.includes('3.8')) ? 'qw38' : ((mlow.includes('35b') || mlow.includes('3.6')) ? 'qw36' : (mlow.includes('gemma') ? 'gemma' : (mlow.includes('lfm') ? 'lfm' : (mlow.includes('phi') ? 'phi' : ((mlow.includes('qwen3-4b') || mlow.includes('qwen3.5')) ? 'qw3' : 'other'))))))));
              const color = item.color || FAMILY_META[fam].color;
              let cats = item.categories;
              if ((!cats || !Object.keys(cats).length) && Array.isArray(item.tasks)) {
                cats = {};
                const byC = {};
                item.tasks.forEach(t => { const ck = t.category || 'general'; (byC[ck] = byC[ck] || []).push(t); });
                Object.entries(byC).forEach(([ck, arr]) => {
                  const pc = arr.filter(t => t.passed).length;
                  cats[ck] = { pass_count: pc, total: arr.length, pass_pct: Number((100 * pc / Math.max(1, arr.length)).toFixed(1)), wall_median_s: Number(medianNum(arr.map(t => t.wall_s || 0)).toFixed(2)) };
                });
              }
              const norm = { ...item, suite: item.suite || 'v1', subset: item.subset || 'all', categories: cats || {}, family_key: fam, family_label: FAMILY_META[fam].label, color, shape: item.shape || (item.draft_model ? 'hex' : 'circle') };
              if (existingIdx >= 0) state.runs[existingIdx] = norm;
              else { state.runs.push(norm); state.addedIds.push(norm.run_id); }
              added++;
            });
          } catch (err) {
            showToast(`Failed to parse ${file.name}`);
          }
        }
        e.target.value = '';
        if (added) { renderAddedList(); renderAll(); showToast(`Imported ${added} run(s)`); }
      });

      document.getElementById('manual-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const msg = document.getElementById('m-form-msg');
        msg.textContent = '';
        const label = document.getElementById('m-label').value.trim();
        const host = document.getElementById('m-host').value.trim() || 'Custom';
        const acc = parseFloat(document.getElementById('m-acc').value);
        const wall = parseFloat(document.getElementById('m-wall').value);
        if (!label || isNaN(acc) || acc < 0 || acc > 100 || isNaN(wall) || wall <= 0) {
          msg.textContent = 'Provide label, Pass@1 (0–100), and Med Wall > 0.';
          return;
        }
        const tok = parseInt(document.getElementById('m-tok').value || '700', 10);
        const pp = parseFloat(document.getElementById('m-pp').value || '400');
        const tg = parseFloat(document.getElementById('m-tg').value || '35');
        const rid = `manual-${Date.now()}`;
        const tgtSuite = (state.suiteGroup === 'combined' || state.suiteGroup === 'frontier') ? 'v3' : state.suiteGroup;
        state.runs.push({
          schema: 'salted-eval/v1', run_id: rid, phase: 'Preview', seed: 20261124,
          suite: tgtSuite,
          host_label: host, family_key: 'other', family_label: 'Custom', backend: 'Manual Preview', model: label, quant: '', effort: 'default', subset: 'all',
          mem_label: '—', color: '#facc15', shape: 'hex',
          summary: {
            pass_count: Math.round((acc / 100) * 16), total_tasks: 16, pass_pct: acc, truncated: 0,
            wall_total_s: Number((wall * 16).toFixed(1)), wall_total_min: Number(((wall * 16) / 60).toFixed(2)),
            wall_median_s: wall, wall_p90_s: null, gen_tok_total: tok * 16, gen_tok_median: tok,
            pp_tps_median: pp, tg_tps_median: tg, draft_accepted: 0, draft_total: 0, draft_accept_pct: null,
          },
          categories: {}, tasks: [],
        });
        state.addedIds.push(rid);
        state.pinnedIds.add(rid);
        renderAddedList();
        renderAll();
        showToast(`Plotted "${label}"`);
      });

      document.getElementById('clear-custom-btn').onclick = () => {
        const rm = new Set(state.addedIds);
        state.runs = state.runs.filter(r => !rm.has(r.run_id));
        state.addedIds = [];
        renderAddedList();
        renderAll();
      };

      document.getElementById('export-csv-btn').onclick = () => {
        const hdr = ['run_id', 'model', 'quant', 'effort', 'suite', 'subset', 'system', 'llama_build', 'pass_pct', 'pass_count', 'total_tasks', 'truncated', 'wall_median_s', 'wall_mean_s', 'wall_total_min', 'gen_tok_median', 'pp_tps_median', 'tg_tps_median', 'draft_accept_pct', 'sys_avg_w', 'sys_wh'];
        const csvCell = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
        const rows = visibleRuns().map(r => {
          const s = r.summary || {};
          const lv = r.llama_version && (r.llama_version.props_build_info || r.llama_version.label);
          const pw = r.power_telemetry && r.power_telemetry.available ? r.power_telemetry : {};
          return [r.run_id, r.model, r.quant, r.effort, r.suite || state.suiteGroup, r.subset || 'all', r.host_label, lv ?? '', s.pass_pct, s.pass_count, s.total_tasks, s.truncated, s.wall_median_s, meanWall(s), s.wall_total_min, s.gen_tok_median, s.pp_tps_median ?? '', s.tg_tps_median ?? '', s.draft_accept_pct ?? '', pw.system_avg_w ?? '', pw.system_wh ?? ''].map(csvCell).join(',');
        });
        const blob = new Blob([[hdr.join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = 'salted-benchmark-summary.csv';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('Exported Summary CSV');
      };

      document.getElementById('export-png-btn').onclick = () => {
        const clone = svgEl.cloneNode(true);
        clone.setAttribute('width', String(W * 2));
        clone.setAttribute('height', String(H * 2));
        const svgBlob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml;charset=utf-8' });
        const url = URL.createObjectURL(svgBlob);
        const img = new Image();
        img.onload = () => {
          const c = document.createElement('canvas'); c.width = W * 2; c.height = H * 2;
          const ctx = c.getContext('2d'); ctx.fillStyle = '#0f172a'; ctx.fillRect(0, 0, c.width, c.height); ctx.drawImage(img, 0, 0, c.width, c.height);
          URL.revokeObjectURL(url);
          const a = document.createElement('a'); a.download = `pareto-${state.mode}-${state.axis}.png`; a.href = c.toDataURL('image/png');
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          showToast('Exported 2× PNG');
        };
        img.src = url;
      };

      function renderChartOnly() {
        if (state.mode === 'overall') renderOverall();
        else if (state.mode === 'category') renderCategory();
      }

      function clamp01(v) { return Math.max(0, Math.min(1, v)); }

      function clientToPlotNorm(clientX, clientY) {
        const rect = svgEl.getBoundingClientRect();
        if (!rect.width || !rect.height) return { fx: 0.5, fy: 0.5, inPlot: false };
        const mx = ((clientX - rect.left) / rect.width) * W;
        const my = ((clientY - rect.top) / rect.height) * H;
        const rawFx = (mx - PAD.left) / PLOT_W;
        const rawFy = 1 - (my - PAD.top) / PLOT_H;
        return {
          fx: clamp01(rawFx),
          fy: clamp01(rawFy),
          inPlot: rawFx >= 0 && rawFx <= 1 && rawFy >= 0 && rawFy <= 1,
        };
      }

      function applyZoomAt(scaleFactor, fx, fy) {
        const z = state.zoom;
        const curW = z.x1 - z.x0, curH = z.y1 - z.y0;
        const nextW = Math.max(0.03, Math.min(1, curW * scaleFactor));
        const nextH = Math.max(0.03, Math.min(1, curH * scaleFactor));
        const dataX = z.x0 + fx * curW;
        const dataY = z.y0 + fy * curH;
        let nx0 = dataX - fx * nextW;
        let ny0 = dataY - fy * nextH;
        nx0 = Math.max(0, Math.min(1 - nextW, nx0));
        ny0 = Math.max(0, Math.min(1 - nextH, ny0));
        state.zoom = { x0: nx0, x1: nx0 + nextW, y0: ny0, y1: ny0 + nextH };
        if (nextW > 0.995 && nextH > 0.995) resetZoom();
        renderChartOnly();
      }

      let lastOutsideWheelTs = 0;
      let lastZoomWheelTs = 0;
      window.addEventListener('wheel', (e) => {
        if (!svgEl.contains(e.target)) lastOutsideWheelTs = Date.now();
      }, { passive: true });

      svgEl.addEventListener('pointerdown', (e) => {
        if (clientToPlotNorm(e.clientX, e.clientY).inPlot) lastOutsideWheelTs = 0;
      });

      svgEl.addEventListener('wheel', (e) => {
        if (state.mode === 'matrix') return;
        const now = Date.now();
        const { fx, fy, inPlot } = clientToPlotNorm(e.clientX, e.clientY);
        if (!inPlot) {
          lastOutsideWheelTs = now;
          return;
        }
        const inZoomBurst = (now - lastZoomWheelTs) < 350;
        const inOutsideBurst = (now - lastOutsideWheelTs) < 220;
        if (!e.ctrlKey && !inZoomBurst && inOutsideBurst) {
          lastOutsideWheelTs = now;
          return;
        }
        // At 1.0x unzoomed, scrolling down (deltaY > 0) cannot zoom out further; let page scroll unless just-zoomed-out
        if (!e.ctrlKey && !isZoomed() && e.deltaY > 0) {
          if (inZoomBurst) e.preventDefault();
          return;
        }
        lastZoomWheelTs = now;
        e.preventDefault();
        hideTooltip();
        const factor = Math.exp(e.deltaY * (e.ctrlKey ? 0.008 : 0.0018));
        applyZoomAt(factor, fx, fy);
      }, { passive: false });

      let pinchDist = null;
      svgEl.addEventListener('touchstart', (e) => {
        if (e.touches.length === 2 && state.mode !== 'matrix') {
          const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
          const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
          if (!clientToPlotNorm(midX, midY).inPlot) { pinchDist = null; return; }
          const dx = e.touches[0].clientX - e.touches[1].clientX;
          const dy = e.touches[0].clientY - e.touches[1].clientY;
          pinchDist = Math.hypot(dx, dy) || 1;
        }
      }, { passive: true });
      svgEl.addEventListener('touchmove', (e) => {
        if (e.touches.length === 2 && pinchDist && state.mode !== 'matrix') {
          const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
          const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
          const { fx, fy, inPlot } = clientToPlotNorm(midX, midY);
          if (!inPlot) return;
          e.preventDefault();
          const dx = e.touches[0].clientX - e.touches[1].clientX;
          const dy = e.touches[0].clientY - e.touches[1].clientY;
          const dist = Math.hypot(dx, dy) || 1;
          applyZoomAt(pinchDist / dist, fx, fy);
          pinchDist = dist;
        }
      }, { passive: false });
      svgEl.addEventListener('touchend', () => { pinchDist = null; });

      let dragState = null;
      svgEl.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || !isZoomed() || state.mode === 'matrix') return;
        dragState = {
          startX: e.clientX, startY: e.clientY,
          z0: { ...state.zoom }, moved: false,
        };
      });
      window.addEventListener('pointermove', (e) => {
        if (!dragState) return;
        const rect = svgEl.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const pxDx = e.clientX - dragState.startX;
        const pxDy = e.clientY - dragState.startY;
        if (!dragState.moved && Math.hypot(pxDx, pxDy) < 4) return;
        dragState.moved = true;
        svgEl.classList.add('dragging');
        hideTooltip();
        const plotPxW = rect.width * (PLOT_W / W);
        const plotPxH = rect.height * (PLOT_H / H);
        const spanX = dragState.z0.x1 - dragState.z0.x0;
        const spanY = dragState.z0.y1 - dragState.z0.y0;
        const dxNorm = -(pxDx / Math.max(1, plotPxW)) * spanX;
        const dyNorm = (pxDy / Math.max(1, plotPxH)) * spanY;
        const nx0 = Math.max(0, Math.min(1 - spanX, dragState.z0.x0 + dxNorm));
        const ny0 = Math.max(0, Math.min(1 - spanY, dragState.z0.y0 + dyNorm));
        state.zoom = { x0: nx0, x1: nx0 + spanX, y0: ny0, y1: ny0 + spanY };
        renderChartOnly();
      });
      window.addEventListener('pointerup', () => {
        if (!dragState) return;
        if (dragState.moved) state.suppressClickUntil = Date.now() + 140;
        dragState = null;
        svgEl.classList.remove('dragging');
      });

      svgEl.addEventListener('dblclick', () => {
        if (!isZoomed() || state.mode === 'matrix') return;
        resetZoom();
        renderChartOnly();
      });
      document.getElementById('reset-zoom-btn').onclick = () => {
        resetZoom();
        renderChartOnly();
      };

      renderAll();
    })();
  </script>
</body>
</html>
"""


def build_html(runs: list[dict], title: str, subtitle: str) -> str:
    runs_json = json.dumps(runs).replace("</", "<\\/")
    return (
        HTML_TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__SUBTITLE__", html.escape(subtitle))
        .replace("__RUNS_JSON__", runs_json)
    )


def selftest() -> None:
    sample = [{
        "schema": "salted-eval/v1",
        "run_id": "test-node1-run",
        "phase": "Phase 1",
        "seed": 20261122,
        "host_label": "node1",
        "url": "http://127.0.0.1:8099/v1/chat/completions",
        "hardware": "8 GB CUDA",
        "engine": "custom-fork/strix-llama.cpp",
        "backend": "CUDA",
        "model": "Inkling-2-Mini",
        "quant": "IQ3_XXS",
        "effort": "low",
        "subset": "all",
        "ctx": 65536,
        "mem_label": "6880 MiB",
        "notes": "/home/alice/llama.cpp on 10.99.0.1",
        "power_telemetry": {"available": True, "host": "node1", "system_avg_w": 110.0, "system_wh": 2.5},
        "summary": {
            "pass_count": 15, "total_tasks": 16, "pass_pct": 93.8, "truncated": 1,
            "wall_total_s": 840.4, "wall_total_min": 14.01, "wall_median_s": 31.38, "wall_p90_s": 103.19,
            "gen_tok_total": 19905, "gen_tok_median": 675, "pp_tps_median": 167.0, "tg_tps_median": 29.6,
            "draft_accepted": 0, "draft_total": 0, "draft_accept_pct": None,
        },
        "categories": {"coding": {"pass_count": 3, "total": 4, "pass_pct": 75.0, "wall_median_s": 79.9}},
        "tasks": [{"id": "code_vm_salt", "category": "coding", "passed": True, "wall_s": 36.0, "pp_tps": 155.5, "tg_tps": 29.3, "predicted_n": 981, "finish_reason": "stop"}],
    }, {
        "schema_version": 1,
        "suite": "agentic",
        "run_id": "agentic-node2-qwflash-med-mtp",
        "host_label": "node2",
        "model": "Qwen3.8-Flash-Next",
        "quant": "UD-Q4_K_XL",
        "effort": "medium",
        "summary": {"pass_count": 10, "total_tasks": 11, "pass_pct": 90.9, "truncated": 0, "wall_total_s": 480.0, "wall_total_min": 8.0, "wall_median_s": 36.4, "gen_tok_total": 14000, "gen_tok_median": 990, "pp_tps_median": None, "tg_tps_median": 29.3},
        "tasks": [{"id": "agent_read_patch_verify", "category": "agent_harness", "passed": True, "wall_s": 36.4, "predicted_n": 990, "tg_tps": 29.3, "finish_reason": "tool_calls"}],
    }]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "run.json"
        p.write_text(json.dumps(sample))
        runs_anon = load_runs([Path(td)], anonymize=True)
        assert len(runs_anon) == 2
        assert runs_anon[0]["host_label"] == "Rig A (8 GB CUDA)"
        assert runs_anon[0]["family_key"] == "inkling"
        assert runs_anon[1]["suite"] == "agentic"
        assert runs_anon[1]["draft_model"] == "mtp"
        assert "agent_harness" in runs_anon[1]["categories"]
        assert "url" not in runs_anon[0]
        assert "/home/alice" not in runs_anon[0]["notes"] and "10.99.0.1" not in runs_anon[0]["notes"]
        out_html = build_html(runs_anon, "Pareto Test", "Anonymized")
        for forbidden in ("node1", "node2", "/home/alice", "10.99.0.1", "127.0.0.1"):
            assert forbidden not in out_html, f"leak found: {forbidden}"
        assert "Rig A (8 GB CUDA)" in out_html
    print("SELFTEST PASSED: pareto-dashboard.py")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build standalone Pareto Frontier HTML dashboard from salted-eval/v1 JSONs.")
    ap.add_argument("inputs", nargs="*", help="Run JSON files or directories (defaults to ~/house/state/benchmarks)")
    ap.add_argument("--out", default="", help="Output HTML file path")
    ap.add_argument("--title", default="Pareto Frontier Telemetry Dashboard")
    ap.add_argument("--subtitle", default="Multi-Phase & Multi-Hardware Salted Eval")
    ap.add_argument("--anonymize", dest="anonymize", action="store_true", default=True, help="Strip hostnames, local paths, and internal IPs (default: on)")
    ap.add_argument("--no-anonymize", dest="anonymize", action="store_false", help="Keep raw local hostnames and paths")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0

    default_dir = Path(__file__).resolve().parents[2] / "state" / "benchmarks"
    paths = [Path(x) for x in args.inputs] if args.inputs else [default_dir]
    runs = load_runs(paths, anonymize=args.anonymize)
    if not runs:
        print(f"ERROR: no salted-eval/v1 runs found in {[str(x) for x in paths]}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else (default_dir / "pareto-dashboard.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(runs, args.title, args.subtitle))
    print(f"WROTE {out_path} ({len(runs)} runs, anonymize={args.anonymize})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
