#!/usr/bin/env python3
"""Rebuild sanitized public HTML & reports and enforce a strict PII/secret leak scan."""
from __future__ import annotations

import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_REGEXES = [
    ("home_path", re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")),
    ("rfc1918_10", re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    ("rfc1918_192", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
    ("cgnat_100", re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b")),
    ("email_addr", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("api_secret", re.compile(r"(?:sk-[A-Za-z0-9]{16,}|gho_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|BEGIN [A-Z ]*PRIVATE KEY)")),
    ("raw_host_label", re.compile(r"\bnode[123]\b", re.I)),
]

# Files where internal runtime target flags (node1/node2) legitimately live for local CLI execution
CLI_RUNNER_FILES = {
    "salted-eval.py",
    "extended_v4_tasks.py",
    "salted-agentic.py",
    "telemetry_power.py",
    "pareto-dashboard.py",
    "analyze_curve.py",
    "refusal-eval.py",
    "model-profiles.json",
    "scripts/publish.py",
    ".githooks/pre-commit",
}


def runtime_redact_words() -> list[str]:
    words = {getpass.getuser(), socket.gethostname().split(".")[0]}
    for w in (os.environ.get("BENCH_REDACT_WORDS") or "").split(","):
        if w.strip():
            words.add(w.strip())
    local_cfg = ROOT / ".redact.local"
    if local_cfg.exists():
        for ln in local_cfg.read_text().splitlines():
            if ln.strip() and not ln.strip().startswith("#"):
                words.add(ln.strip())
    return sorted(w for w in words if len(w) >= 3 and w not in ("root", "user", "localhost"))


def scan_file(path: Path, rel: str, extra: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    errs: list[str] = []
    for w in extra:
        if rel in CLI_RUNNER_FILES and re.fullmatch(r"node[123]", w, re.I):
            continue
        if re.search(rf"\b{re.escape(w)}\b", text, re.I):
            errs.append(f"{rel}: forbidden local identifier {w!r}")
    for tag, rx in FORBIDDEN_REGEXES:
        if tag == "raw_host_label" and rel in CLI_RUNNER_FILES:
            continue
        for m in rx.finditer(text):
            val = m.group(0)
            if rel in ("pareto-dashboard.py", "scripts/publish.py") and val in ("/home/alice", "10.99.0.1"):
                continue
            errs.append(f"{rel}: [{tag}] matched {val!r}")
    return errs


def check_html_js(html_path: Path) -> None:
    txt = html_path.read_text(encoding="utf-8")
    m = re.search(r"<script>\s*(.*)\s*</script>", txt, re.S)
    if not m:
        raise RuntimeError(f"Missing <script> block in {html_path}")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(m.group(1))
        jp = tf.name
    try:
        subprocess.run(["node", "--check", jp], check=True)
    finally:
        Path(jp).unlink(missing_ok=True)


def main() -> int:
    extra = runtime_redact_words()
    if extra:
        os.environ["BENCH_REDACT_WORDS"] = ",".join(extra)
    runs_dir = ROOT / "runs"
    dash_html = ROOT / "salted-pareto-dashboard.html"
    idx_html = ROOT / "index.html"
    curve_md = ROOT / "bench-ink-qw38-curve.md"

    if runs_dir.exists() and any(runs_dir.glob("*.json")):
        subprocess.run([
            sys.executable, str(ROOT / "pareto-dashboard.py"), str(runs_dir),
            "--out", str(dash_html),
            "--title", "Salted Benchmark Pareto Frontier",
            "--subtitle", "Local Quantization & Hardware Topology Comparison (Anonymized)",
        ], check=True)
        shutil.copyfile(dash_html, idx_html)
        subprocess.run([
            sys.executable, str(ROOT / "analyze_curve.py"),
            "--runs", str(runs_dir), "--out-md", str(curve_md),
        ], check=True)

    check_html_js(dash_html)
    check_html_js(idx_html)

    failures: list[str] = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if rel.startswith(".git/") or rel.startswith("runs/") or rel.startswith("runs_v4/") or rel.startswith("__pycache__/"):
            continue
        if p.suffix in (".pyc", ".log") or p.name == ".redact.local":
            continue
        failures.extend(scan_file(p, rel, extra))

    if failures:
        print("LEAK CHECK FAILED:", file=sys.stderr)
        for f in failures[:40]:
            print(" ", f, file=sys.stderr)
        return 1
    print("PUBLISH CHECK PASSED: HTML JS valid & 0 PII/secret leaks across public repo files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
