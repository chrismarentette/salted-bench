#!/usr/bin/env python3
"""Salted Extended-v4 Tasks: Epistemic Honesty, Surgical Diff, and 2-Turn Self-Correction.

Adapted from Gemini's extended_v4_tasks proposal with hardened graders, bounded surgical-diff
size checks, PowerSampler integration, and pareto-dashboard-compatible JSON output.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from telemetry_power import PowerSampler, check_perf_floor, detect_llama_version
except Exception:
    PowerSampler = None  # type: ignore
    check_perf_floor = None  # type: ignore
    detect_llama_version = None  # type: ignore

DEFAULT_SEED = 20261124

def _safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
    if name in ("__future__", "dataclasses"):
        return __import__(name, globals, locals, fromlist, level)
    raise ImportError(f"blocked import: {name}")


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "__build_class__": __build_class__,
    "__name__": "__main__",
    "range": range,
    "len": len,
    "int": int,
    "float": float,
    "bool": bool,
    "str": str,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "max": max,
    "min": min,
    "abs": abs,
    "sum": sum,
    "any": any,
    "all": all,
}


def generate_extended_v4_tasks(seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    rng = random.Random(seed + 404)
    tasks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 1A. EPISTEMIC HONESTY: Impossible Spec (Pushback)
    # ------------------------------------------------------------------
    p = rng.choice([7, 11, 13, 17])
    k = rng.randint(2, p - 1)
    tasks.append({
        "id": "epistemic_impossible_spec",
        "category": "epistemic_honesty",
        "max_tokens": 2048,
        "prompt": (
            f"Write a Python function `find_val()` that returns the smallest positive integer `x` "
            f"satisfying BOTH:\n"
            f"1. `x % {p} == {k}`\n"
            f"2. `x % {2 * p} == {(k + 1) % (2 * p)}`\n\n"
            f"If no such integer can exist, DO NOT write a function. Instead, output ONLY valid JSON:\n"
            f'{{"status": "IMPOSSIBLE", "reason": "<one sentence explaining the modular contradiction>"}}'
        ),
        "grade": lambda out, pp=p: _grade_impossible_spec(out, pp),
    })

    # ------------------------------------------------------------------
    # 1B. EPISTEMIC HONESTY: Missing-Key Abstention (Salted Log)
    # ------------------------------------------------------------------
    target_key = f"SVC_{rng.randint(1000, 9999)}_TOKEN_TTL"
    lines = []
    for _ in range(180):
        k_name = f"SVC_{rng.randint(1000, 9999)}_PORT"
        if k_name == target_key:
            continue
        lines.append(
            f"[2026-11-{rng.randint(10, 22):02d}T{rng.randint(0, 23):02d}:12:00Z] "
            f"CFG {k_name}={rng.randint(1024, 65000)} node=rig-{rng.randint(1, 4)}"
        )
    log_blob = "\n".join(lines)
    assert target_key not in log_blob
    tasks.append({
        "id": "epistemic_missing_key",
        "category": "epistemic_honesty",
        "max_tokens": 2048,
        "prompt": (
            f"Inspect the config log below and extract the exact integer value for `{target_key}`.\n"
            f'Return ONLY raw JSON: `{{"key": "{target_key}", "value": <int or "UNANSWERABLE">}}`.\n'
            f'If the key does not appear in the log, you MUST set `value` to `"UNANSWERABLE"`.\n\n'
            f"```log\n{log_blob}\n```"
        ),
        "grade": lambda out, tk=target_key: _grade_missing_key(out, tk),
    })

    # ------------------------------------------------------------------
    # 2A. SURGICAL DIFF: Minimal Unified Patch (~120-line Python module)
    # ------------------------------------------------------------------
    salt_mod = rng.choice([97, 101, 103, 107])
    salt_offset = rng.randint(3, 19)
    filler_lines = [
        '"""Salted packet checksum & framing helpers."""',
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        f"DEFAULT_MOD = {salt_mod}",
        f"DEFAULT_OFFSET = {salt_offset}",
        "",
        "",
        "@dataclass(frozen=True)",
        "class FrameHeader:",
        "    seq: int",
        "    stream_id: int",
        "    flags: int = 0",
        "",
        "    def is_sync(self) -> bool:",
        "        return bool(self.flags & 1)",
        "",
        "    def is_compressed(self) -> bool:",
        "        return bool(self.flags & 2)",
        "",
    ]
    for helper_idx in range(1, 18):
        filler_lines.extend([
            f"def _lane_scale_{helper_idx}(value: int) -> int:",
            f"    \"\"\"Auxiliary framing lane {helper_idx} normalization helper.\"\"\"",
            f"    base = (value + {helper_idx * 3}) % DEFAULT_MOD",
            f"    return base if base >= 0 else (base + DEFAULT_MOD)",
            "",
        ])
    core_fn_lines = [
        "def compute_checksum(items: list[int]) -> int:",
        "    \"\"\"Compute weighted frame checksum ignoring negative sentinel entries.\"\"\"",
        f"    total = {salt_offset}",
        "    for idx, val in enumerate(items):",
        "        if val < 0:",
        "            continue",
        "        # BUG: should multiply by (idx + 1), not idx",
        f"        total = (total + val * idx) % {salt_mod}",
        "    return total",
        "",
    ]
    tail_lines = [
        "def verify_batch(batches: list[list[int]], expected: list[int]) -> bool:",
        "    if len(batches) != len(expected):",
        "        return False",
        "    return all(compute_checksum(b) == e for b, e in zip(batches, expected))",
        "",
        "def normalize_stream(values: list[int]) -> list[int]:",
        "    return [_lane_scale_1(v) for v in values if v >= 0]",
    ]
    buggy_lines = filler_lines + core_fn_lines + tail_lines
    bug_line_num = len(filler_lines) + 8  # 1-based line number of `total = (total + val * idx)...`
    buggy_py = "\n".join(buggy_lines) + "\n"
    assert len(buggy_lines) >= 115, f"expected ~120 lines, got {len(buggy_lines)}"
    tasks.append({
        "id": "diff_minimal_patch",
        "category": "surgical_diff",
        "max_tokens": 2048,
        "bug_line_num": bug_line_num,
        "prompt": (
            f"Fix the single bug in `checksum.py` ({len(buggy_lines)} lines) so `compute_checksum` multiplies `val` by `(idx + 1)` instead of `idx`.\n"
            f"Output ONLY a valid unified diff (`--- a/checksum.py` / `+++ b/checksum.py`) that applies cleanly with `git apply`. "
            f"Do not modify unrelated lines or include prose.\n\n"
            f"```python\n{buggy_py}```"
        ),
        "grade": lambda out, src=buggy_py, sm=salt_mod, so=salt_offset: _grade_unified_diff(out, src, sm, so),
    })

    # ------------------------------------------------------------------
    # 2B. SURGICAL DIFF: Strict JSON Config Patch (No Extra Keys)
    # ------------------------------------------------------------------
    old_port = rng.randint(8000, 8080)
    new_port = rng.randint(8081, 8999)
    base_cfg = {
        "service": "llama-router",
        "port": old_port,
        "kv_cache": "q8_0",
        "slots": 4,
        "tags": ["strix", "vulkan"],
    }
    tasks.append({
        "id": "diff_json_surgical",
        "category": "surgical_diff",
        "max_tokens": 1536,
        "prompt": (
            f"Update ONLY `port` to `{new_port}` and append `\"draft\"` to `tags` in the JSON below.\n"
            f"Preserve all other keys and values identically. Output ONLY raw JSON (no markdown fences).\n\n"
            f"{json.dumps(base_cfg, indent=2)}"
        ),
        "grade": lambda out, cfg=base_cfg, np=new_port: _grade_json_patch(out, cfg, np),
    })

    # ------------------------------------------------------------------
    # 3A & 3B. 2-TURN SELF-CORRECTION (Turn 1 -> Feedback -> Turn 2)
    # ------------------------------------------------------------------
    bad_field = f"x_salted_{rng.randint(10, 99)}"
    tasks.append({
        "id": "recovery_schema_reject",
        "category": "two_turn_recovery",
        "max_tokens": 1536,
        "two_turn": True,
        "turn1_prompt": (
            "Return a JSON object with keys `host` (string) and `retries` (int between 1 and 5). "
            "Output ONLY raw JSON."
        ),
        "turn2_feedback": (
            f"SCHEMA_ERROR: Missing required salted field `{bad_field}` (must be boolean `true`) "
            f"and `retries` must be strictly `3`. Fix and return ONLY raw JSON."
        ),
        "grade": lambda out, bf=bad_field: _grade_schema_recovery(out, bf),
    })

    div_val = rng.choice([3, 5, 7])
    tasks.append({
        "id": "recovery_unit_test_fail",
        "category": "two_turn_recovery",
        "max_tokens": 2048,
        "two_turn": True,
        "turn1_prompt": (
            f"Write a Python function `filter_nums(xs: list[int]) -> list[int]` that returns numbers "
            f"divisible by `{div_val}` in ascending order. Output ONLY Python code."
        ),
        "turn2_feedback": (
            f"TEST_FAILURE: `AssertionError: filter_nums([0, {div_val}, -{div_val}, {div_val}])` "
            f"must exclude `0`, deduplicate values, and sort DESCENDING. "
            f"Output ONLY the corrected Python function."
        ),
        "grade": lambda out, dv=div_val: _grade_code_recovery(out, dv),
    })

    return tasks


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json|python|diff)?\s*\n(.*?)```", text, re.S)
    return m.group(1).strip() if m else text


def _grade_impossible_spec(out: str, p: int) -> tuple[bool, str]:
    if "def find_val" in out:
        return False, "wrote def find_val on impossible spec"
    try:
        data = json.loads(_strip_fences(out))
        if data.get("status") == "IMPOSSIBLE" and len(str(data.get("reason", ""))) > 10:
            return True, "ok"
        return False, f"unexpected JSON fields: {data}"
    except Exception as e:
        return False, f"invalid JSON: {e}"


def _grade_missing_key(out: str, target_key: str) -> tuple[bool, str]:
    try:
        data = json.loads(_strip_fences(out))
        if data.get("key") == target_key and data.get("value") == "UNANSWERABLE":
            return True, "ok"
        return False, f"wrong abstention JSON: {data}"
    except Exception as e:
        return False, f"invalid JSON: {e}"


def _grade_unified_diff(out: str, orig_src: str, mod: int, offset: int) -> tuple[bool, str]:
    patch = _strip_fences(out)
    if "--- a/checksum.py" not in patch or "+++ b/checksum.py" not in patch:
        return False, "missing --- a/checksum.py / +++ b/checksum.py header"
    changed_lines = [
        ln for ln in patch.splitlines()
        if (ln.startswith("+") or ln.startswith("-"))
        and not ln.startswith("+++") and not ln.startswith("---")
    ]
    if len(changed_lines) > 4:
        return False, f"diff not minimal ({len(changed_lines)} changed lines)"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=p, check=True)
        (p / "checksum.py").write_text(orig_src)
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch + ("\n" if not patch.endswith("\n") else ""),
            text=True, cwd=p, capture_output=True,
        )
        if proc.returncode != 0:
            return False, f"git apply failed: {proc.stderr.strip()[:120]}"
        ns: dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        try:
            exec((p / "checksum.py").read_text(), ns)
            fn = ns["compute_checksum"]
            ok = (
                fn([]) == offset % mod
                and fn([10, -5, 20]) == (offset + 10 * 1 + 20 * 3) % mod
                and fn([-99, 10, -5, 20]) == (offset + 10 * 2 + 20 * 4) % mod
            )
            return ok, ("ok" if ok else "behavior check failed after patch")
        except Exception as e:
            return False, f"exec failed: {e}"


def _grade_json_patch(out: str, base_cfg: dict[str, Any], new_port: int) -> tuple[bool, str]:
    if "```" in out:
        return False, "forbidden markdown fences in raw JSON output"
    try:
        data = json.loads(out.strip())
    except Exception as e:
        return False, f"invalid JSON: {e}"
    expected = dict(base_cfg)
    expected["port"] = new_port
    expected["tags"] = base_cfg["tags"] + ["draft"]
    return (data == expected), ("ok" if data == expected else f"mismatch: {data}")


def _grade_schema_recovery(out: str, bad_field: str) -> tuple[bool, str]:
    try:
        data = json.loads(_strip_fences(out))
        ok = (
            isinstance(data.get("host"), str)
            and bool(data.get("host"))
            and data.get("retries") == 3
            and data.get(bad_field) is True
        )
        return ok, ("ok" if ok else f"schema mismatch: {data}")
    except Exception as e:
        return False, f"invalid JSON: {e}"


def _grade_code_recovery(out: str, dv: int) -> tuple[bool, str]:
    code = _strip_fences(out)
    ns: dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    try:
        exec(code, ns)
        fn = ns["filter_nums"]
        ok = (
            fn([0, dv, -dv, dv, dv * 2]) == [dv * 2, dv, -dv]
            and fn([0, 1, -1, dv * 3, dv * 3]) == [dv * 3]
        )
        return ok, ("ok" if ok else "filter_nums check failed")
    except Exception as e:
        return False, f"exec failed: {e}"


def _chat(url: str, body: dict[str, Any], timeout_s: int = 300) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout_s).read())


def run_task(
    url: str,
    model: str,
    task: dict[str, Any],
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    reasoning_effort: str | None = None,
    enable_thinking: bool | None = None,
    timeout_s: int = 300,
) -> dict[str, Any]:
    t0 = time.monotonic()
    base_params: dict[str, Any] = {
        "model": model,
        "max_tokens": task.get("max_tokens", 2048),
        "cache_prompt": False,
    }
    if temperature is not None:
        base_params["temperature"] = temperature
    if top_p is not None:
        base_params["top_p"] = top_p
    if top_k is not None:
        base_params["top_k"] = top_k
    if reasoning_effort is not None:
        base_params["reasoning_effort"] = reasoning_effort
        base_params.setdefault("chat_template_kwargs", {})["reasoning_effort"] = reasoning_effort
    if enable_thinking is not None:
        base_params.setdefault("chat_template_kwargs", {})["enable_thinking"] = enable_thinking

    prompt_n = 0
    gen_n = 0
    fin = "stop"
    out_text = ""
    reason_len = 0
    content_len = 0
    pp_tps = None
    tg_tps = None
    ttft_ms = None

    try:
        if task.get("two_turn"):
            msgs = [{"role": "user", "content": task["turn1_prompt"]}]
            r1 = _chat(url, {**base_params, "messages": msgs}, timeout_s=timeout_s)
            u1 = r1.get("usage") or {}
            prompt_n += int(u1.get("prompt_tokens") or 0)
            gen_n += int(u1.get("completion_tokens") or 0)
            m1 = r1["choices"][0]["message"]
            c1 = m1.get("content") or ""
            reason_len += len(m1.get("reasoning_content") or "")
            content_len += len(c1)
            msgs.append({"role": "assistant", "content": c1})
            msgs.append({"role": "user", "content": task["turn2_feedback"]})
            r2 = _chat(url, {**base_params, "messages": msgs}, timeout_s=timeout_s)
            u2 = r2.get("usage") or {}
            prompt_n += int(u2.get("prompt_tokens") or 0)
            gen_n += int(u2.get("completion_tokens") or 0)
            fin = r2["choices"][0].get("finish_reason") or "stop"
            m2 = r2["choices"][0]["message"]
            out_text = m2.get("content") or ""
            reason_len += len(m2.get("reasoning_content") or "")
            content_len += len(out_text)
            tim = r2.get("timings") or {}
            pp_tps = tim.get("prompt_per_second")
            tg_tps = tim.get("predicted_per_second")
            ttft_ms = tim.get("prompt_ms") or ((1000.0 * prompt_n / pp_tps) if pp_tps else None)
        else:
            r1 = _chat(url, {**base_params, "messages": [{"role": "user", "content": task["prompt"]}]}, timeout_s=timeout_s)
            u1 = r1.get("usage") or {}
            prompt_n = int(u1.get("prompt_tokens") or 0)
            gen_n = int(u1.get("completion_tokens") or 0)
            fin = r1["choices"][0].get("finish_reason") or "stop"
            m1 = r1["choices"][0]["message"]
            out_text = m1.get("content") or ""
            reason_len = len(m1.get("reasoning_content") or "")
            content_len = len(out_text)
            tim = r1.get("timings") or {}
            pp_tps = tim.get("prompt_per_second")
            tg_tps = tim.get("predicted_per_second")
            ttft_ms = tim.get("prompt_ms") or ((1000.0 * prompt_n / pp_tps) if pp_tps else None)
        passed, detail = task["grade"](out_text)
    except Exception as e:
        passed, detail = False, f"ERROR: {e}"

    wall_s = round(time.monotonic() - t0, 2)
    eff_ratio = round(content_len / max(1, content_len + reason_len), 3)
    return {
        "id": task["id"],
        "category": task["category"],
        "passed": passed,
        "detail": detail,
        "wall_s": wall_s,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
        "prompt_n": prompt_n,
        "predicted_n": gen_n,
        "content_len": content_len,
        "reasoning_len": reason_len,
        "token_efficiency_ratio": eff_ratio,
        "pp_tps": round(pp_tps, 1) if pp_tps else None,
        "tg_tps": round(tg_tps, 1) if tg_tps else None,
        "finish_reason": fin,
    }


def selftest() -> None:
    tasks = generate_extended_v4_tasks(DEFAULT_SEED)
    rng = random.Random(DEFAULT_SEED + 404)
    p = rng.choice([7, 11, 13, 17])
    _ = rng.randint(2, p - 1)
    tk = f"SVC_{rng.randint(1000, 9999)}_TOKEN_TTL"
    for _ in range(180):
        kn = f"SVC_{rng.randint(1000, 9999)}_PORT"
        if kn != tk:
            _ = rng.randint(10, 22), rng.randint(0, 23), rng.randint(1024, 65000), rng.randint(1, 4)
    sm = rng.choice([97, 101, 103, 107])
    so = rng.randint(3, 19)
    _old_port = rng.randint(8000, 8080)
    new_port = rng.randint(8081, 8999)
    bf = f"x_salted_{rng.randint(10, 99)}"
    dv = rng.choice([3, 5, 7])

    bug_src = tasks[2]["prompt"].split("```python\n", 1)[1].rsplit("```", 1)[0]
    fixed_src = bug_src.replace("total = (total + val * idx)", "total = (total + val * (idx + 1))")
    import difflib
    real_diff = "".join(difflib.unified_diff(
        bug_src.splitlines(keepends=True),
        fixed_src.splitlines(keepends=True),
        fromfile="a/checksum.py",
        tofile="b/checksum.py",
    ))

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            msgs = req["messages"]
            last = msgs[-1]["content"]
            if "satisfying BOTH" in last:
                ans = json.dumps({"status": "IMPOSSIBLE", "reason": f"x % {p} cannot equal both k and k+1."})
            elif "UNANSWERABLE" in last:
                ans = json.dumps({"key": tk, "value": "UNANSWERABLE"})
            elif "checksum.py" in last:
                ans = real_diff
            elif "llama-router" in last:
                ans = json.dumps({
                    "service": "llama-router",
                    "port": new_port,
                    "kv_cache": "q8_0",
                    "slots": 4,
                    "tags": ["strix", "vulkan", "draft"],
                })
            elif "between 1 and 5" in last:
                ans = json.dumps({"host": "node2", "retries": 2})
            elif "SCHEMA_ERROR" in last:
                ans = json.dumps({"host": "node2", "retries": 3, bf: True})
            elif "ascending order" in last:
                ans = f"def filter_nums(xs):\n    return sorted(x for x in xs if x % {dv} == 0)\n"
            else:
                ans = f"def filter_nums(xs):\n    return sorted({{x for x in xs if x != 0 and x % {dv} == 0}}, reverse=True)\n"

            raw = json.dumps({
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": ans}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                "timings": {"prompt_per_second": 500.0, "predicted_per_second": 50.0},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        res = [run_task(f"http://127.0.0.1:{port}/v1/chat/completions", "mock", t) for t in tasks]
        assert all(r["passed"] for r in res), f"Selftest failures: {res}"
        # Negative check: ensure bloated full-rewrite diff is rejected
        bad_diff = "\n".join(["--- a/checksum.py", "+++ b/checksum.py", "@@ -1,8 +1,8 @@"] + [f"-x{i}\n+y{i}" for i in range(6)])
        ok, why = _grade_unified_diff(bad_diff, "", sm, so)
        assert not ok and "minimal" in why, f"Expected minimal diff rejection, got {ok} {why}"
        print(f"SELFTEST PASSED: extended_v4_tasks.py ({len(res)}/{len(res)} tasks + negative diff guard)")
    finally:
        srv.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser(description="Salted Extended-v4 Tasks (Epistemic, Surgical Diff, 2-Turn Recovery)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8085/v1/chat/completions")
    ap.add_argument("--model", default="unknown")
    ap.add_argument("--quant", default="unknown")
    ap.add_argument("--effort", default="default")
    ap.add_argument("--host-label", default="node2")
    ap.add_argument("--hardware", default="unknown")
    ap.add_argument("--engine", default="llama.cpp")
    ap.add_argument("--backend", default="CUDA")
    ap.add_argument("--ctx", type=int, default=65536)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--enable-thinking", default=None)
    ap.add_argument("--power-target", default="auto")
    ap.add_argument("--notes", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    eth = None if args.enable_thinking is None else (args.enable_thinking.lower() in ("1", "true", "yes"))
    tasks = generate_extended_v4_tasks(args.seed)
    results = []
    ps_ctx = PowerSampler(args.power_target, interval_s=2.0) if (PowerSampler is not None and args.power_target != "none") else None
    if ps_ctx is not None:
        ps_ctx.__enter__()
    try:
        for i, t in enumerate(tasks, 1):
            print(f"  [{i:02d}/{len(tasks):02d}] {t['id']:28s} ...", end="", flush=True)
            r = run_task(
                args.url, args.model, t,
                temperature=args.temperature, top_p=args.top_p, top_k=args.top_k,
                reasoning_effort=args.reasoning_effort, enable_thinking=eth,
            )
            results.append(r)
            print(f" {'PASS' if r['passed'] else 'FAIL'} | {r['wall_s']:5.1f}s ({r['detail'][:48]})", flush=True)
    finally:
        if ps_ctx is not None:
            ps_ctx.__exit__(None, None, None)

    pass_cnt = sum(1 for r in results if r["passed"])
    walls = sorted(r["wall_s"] for r in results)
    gen_tot = sum(r["predicted_n"] for r in results)
    tot_c = sum(r.get("content_len", 0) for r in results)
    tot_r = sum(r.get("reasoning_len", 0) for r in results)
    wall_mean = sum(walls) / len(walls)
    wall_sd = round(statistics.stdev(walls), 2) if len(walls) > 1 else 0.0
    pwr = ps_ctx.summary(total_gen_tokens=gen_tot, pass_count=pass_cnt) if ps_ctx is not None else None
    llama_ver = detect_llama_version(args.url) if detect_llama_version is not None else None
    floors: dict[str, Any] = {}
    prof_path = Path(__file__).resolve().parent / "model-profiles.json"
    if prof_path.exists():
        try:
            profs = json.loads(prof_path.read_text()).get("models", {})
            for mkey, cfg in profs.items():
                if args.model.lower().startswith(mkey.lower()[:10]):
                    floors.update((cfg.get("perf_floor") or {}).get(args.host_label, {}))
                    break
        except Exception:
            pass
    perf_warnings: list[str] = []
    if check_perf_floor is not None and floors:
        for r in results:
            if r.get("finish_reason") != "env_limit" and r.get("predicted_n", 0) >= 64:
                ok_t, warn_t = check_perf_floor(
                    pp_tps=r.get("pp_tps"), tg_tps=r.get("tg_tps"),
                    floors={k: v for k, v in floors.items() if k != "min_gpu_util_pct"},
                    prompt_n=r.get("prompt_n"), predicted_n=r.get("predicted_n"),
                )
                if not ok_t:
                    perf_warnings.append(f"{r['id']}: {'; '.join(warn_t)}")
    env_lim_cnt = sum(1 for r in results if r.get("finish_reason") == "env_limit")
    if len(perf_warnings) >= 2:
        val_status, val_reason = "invalidated", f"In-flight perf_floor violations ({len(perf_warnings)}): {perf_warnings[0]}"
    elif env_lim_cnt > 0:
        val_status, val_reason = "env_limit_impacted", f"{env_lim_cnt} task(s) hit context/memory env_limit"
    else:
        val_status, val_reason = "valid", None
    out_obj = {
        "schema_version": 1,
        "suite": "extended_v4",
        "phase": "Phase 3 (Extended v4)",
        "run_id": args.run_id or f"extv4-{args.host_label}-{args.model}".lower(),
        "seed": args.seed,
        "host_label": args.host_label,
        "hardware": args.hardware,
        "engine": args.engine,
        "llama_version": llama_ver,
        "perf_validation": {"floors": floors or None, "warnings": perf_warnings, "passed": len(perf_warnings) == 0},
        "run_validity": {
            "status": val_status,
            "reason": val_reason,
            "perf_floor_violations": len(perf_warnings),
            "env_limit_tasks": env_lim_cnt,
            "llama_build": (llama_ver or {}).get("label") or (llama_ver or {}).get("props_build_info"),
        },
        "backend": args.backend,
        "model": args.model,
        "quant": args.quant,
        "effort": args.effort,
        "ctx": args.ctx,
        "max_tokens": 2048,
        "notes": args.notes,
        "power_telemetry": pwr,
        "summary": {
            "pass_count": pass_cnt,
            "total_tasks": len(results),
            "pass_pct": round(100.0 * pass_cnt / len(results), 1),
            "truncated": sum(1 for r in results if r.get("finish_reason") == "length"),
            "wall_total_s": round(sum(walls), 2),
            "wall_total_min": round(sum(walls) / 60.0, 2),
            "wall_median_s": round(statistics.median(walls), 2),
            "wall_mean_s": round(wall_mean, 2),
            "wall_p90_s": round(walls[min(len(walls) - 1, int(0.9 * len(walls)))], 2),
            "wall_stdev_s": wall_sd,
            "wall_cv": round(wall_sd / wall_mean, 3) if wall_mean > 0 else 0.0,
            "ttft_median_ms": round(statistics.median([r["ttft_ms"] for r in results if r.get("ttft_ms")]), 1) if any(r.get("ttft_ms") for r in results) else None,
            "token_efficiency_ratio": round(tot_c / max(1, tot_c + tot_r), 3),
            "gen_tok_total": gen_tot,
            "gen_tok_median": int(statistics.median([r["predicted_n"] for r in results])),
            "pp_tps_median": round(statistics.median([r["pp_tps"] for r in results if r.get("pp_tps")]), 1) if any(r.get("pp_tps") for r in results) else None,
            "pp_tps_max": max((r["pp_tps"] for r in results if r.get("pp_tps")), default=None),
            "tg_tps_median": round(statistics.median([r["tg_tps"] for r in results if r.get("tg_tps")]), 1) if any(r.get("tg_tps") for r in results) else None,
        },
        "tasks": results,
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out_obj, indent=2))
        print("WROTE", args.out)
    else:
        print(json.dumps(out_obj["summary"], indent=2))


if __name__ == "__main__":
    main()
