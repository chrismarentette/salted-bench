#!/usr/bin/env python3
# JIG: tier=drawer | mode=ro | nodes=node1,node2 | tags=benchmark,salted,eval,llama,moe,pareto
# SUMMARY: Seeded anti-contamination 16/24/28-task eval emitting salted-eval/v1 JSON bundles.
# USE: Run against any OpenAI-compatible llama-server (`--url`, `--model`, `--seed`, `--suite`, `--subset`, `--out`). Self-test: `--self-test`.
"""salted-eval.py — Salted, PRNG-seeded quality + wall-clock benchmark.

Generates 16 deterministic anti-contamination tasks (Twisted Coding, Symbolic
Reasoning, Strict Schema & Negative Constraints, Long Context 4k-24k) from a
seed and grades responses automatically. Emits a normalized `salted-eval/v1`
JSON run bundle suitable for `pareto-dashboard.py`.
"""
import argparse
import hashlib
import heapq
import itertools
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from telemetry_power import PowerSampler, check_perf_floor, detect_llama_version, sample_once
except Exception:
    PowerSampler = None
    check_perf_floor = None
    detect_llama_version = None
    sample_once = None

SEED = 20261124
URL = "http://127.0.0.1:8099/v1/chat/completions"


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, flags=re.S | re.I)
    if blocks:
        return blocks[-1].strip()
    return text.strip()


def extract_json_obj(text: str):
    s = text.strip()
    # Try exact raw first (needed for strict no-fence tests)
    try:
        return json.loads(s), True
    except Exception:
        pass
    m = re.findall(r"```(?:json)?\s*\n(.*?)```", s, flags=re.S | re.I)
    if m:
        try:
            return json.loads(m[-1].strip()), False
        except Exception:
            pass
    # Fallback first {..} or [..]
    for pat in (r"(\{.*\})", r"(\[.*\])"):
        mm = re.search(pat, s, flags=re.S)
        if mm:
            try:
                return json.loads(mm.group(1)), False
            except Exception:
                pass
    return None, False


def run_python_tests(code: str, test_harness: str) -> tuple[bool, str]:
    full = code + "\n\n" + test_harness + "\n"
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", full],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            return True, "ok"
        return False, (proc.stderr or proc.stdout).strip()[-300:]
    except Exception as e:
        return False, str(e)


def build_tasks(seed: int = SEED):
    rng = random.Random(seed)
    tasks = []

    # ------------------------------------------------------------------
    # CAT 1: TWISTED / COUNTERFACTUAL CODING (4 tasks, unittest verified)
    # ------------------------------------------------------------------

    # 1. code_vm_salt: custom opcodes + prime modulus + conditional skip
    ops = rng.sample(["0x1D", "0x3A", "0x57", "0x7E", "0x91", "0xB4", "0xC9", "0xE2"], 5)
    op_add, op_xor_rot, op_swap_sub, op_skip_odd, op_mul_acc = ops
    mod_p = rng.choice([89, 97, 101, 103])
    rot_k = rng.choice([2, 3, 5])
    vm_prompt = textwrap.dedent(f"""\
        Write a Python function `run_vm(program, regs)` where `program` is a list of `(opcode_hex_str, arg_int)` pairs and `regs` is a 3-element list `[r0, r1, r2]` of non-negative integers.
        All register results are kept modulo `{mod_p}` (using Python `% {mod_p}`) after every instruction.
        Start at instruction pointer `ip = 0` and execute until `ip >= len(program)` (stop after at most 500 steps).
        Exact opcode definitions (case-insensitive hex string matching):
        - `{op_add}`: `r0 = (r0 + r1 + arg) % {mod_p}`; `ip += 1`
        - `{op_xor_rot}`: `r1 = ((r1 ^ arg) * {rot_k}) % {mod_p}`; `ip += 1`
        - `{op_swap_sub}`: swap `r0` and `r2`, then set `r2 = (r2 - arg) % {mod_p}`; `ip += 1`
        - `{op_skip_odd}`: if `(r0 + r2)` is odd, `ip += 2`, else `ip += 1` (ignores `arg`)
        - `{op_mul_acc}`: `r2 = (r2 + r0 * r1 + arg) % {mod_p}`; `ip += 1`
        Return `[r0, r1, r2]`. Do not mutate the caller's `regs` list. Output only Python code in a ```python block.""")

    def ref_vm(program, regs, _ops=ops, _mod=mod_p, _rot=rot_k):
        oa, ox, os_, osk, om = [x.lower() for x in _ops]
        r0, r1, r2 = [x % _mod for x in regs]
        ip = 0
        steps = 0
        while ip < len(program) and steps < 500:
            steps += 1
            op, arg = program[ip][0].lower(), program[ip][1]
            if op == oa:
                r0 = (r0 + r1 + arg) % _mod
                ip += 1
            elif op == ox:
                r1 = ((r1 ^ arg) * _rot) % _mod
                ip += 1
            elif op == os_:
                r0, r2 = r2, r0
                r2 = (r2 - arg) % _mod
                ip += 1
            elif op == osk:
                ip += 2 if ((r0 + r2) % 2 == 1) else 1
            elif op == om:
                r2 = (r2 + r0 * r1 + arg) % _mod
                ip += 1
            else:
                raise ValueError(op)
        return [r0, r1, r2]

    vm_cases = []
    for _ in range(6):
        prog = [(rng.choice(ops), rng.randint(1, 40)) for _ in range(rng.randint(8, 15))]
        init = [rng.randint(0, 80) for _ in range(3)]
        vm_cases.append((prog, init, ref_vm(prog, init)))
    vm_harness = f"""
cases = {repr(vm_cases)}
for prog, init, exp in cases:
    snap = list(init)
    got = run_vm(prog, init)
    assert init == snap, "mutated input regs"
    assert got == exp, f"expected {{exp}}, got {{got}}"
"""
    tasks.append({
        "id": "code_vm_salt",
        "category": "coding",
        "prompt": vm_prompt,
        "ref_code": textwrap.dedent(f"""\
            def run_vm(program, regs):
                r0, r1, r2 = [x % {mod_p} for x in regs]
                ip = 0
                steps = 0
                while ip < len(program) and steps < 500:
                    steps += 1
                    op, arg = program[ip][0].lower(), program[ip][1]
                    if op == {op_add.lower()!r}:
                        r0 = (r0 + r1 + arg) % {mod_p}; ip += 1
                    elif op == {op_xor_rot.lower()!r}:
                        r1 = ((r1 ^ arg) * {rot_k}) % {mod_p}; ip += 1
                    elif op == {op_swap_sub.lower()!r}:
                        r0, r2 = r2, r0; r2 = (r2 - arg) % {mod_p}; ip += 1
                    elif op == {op_skip_odd.lower()!r}:
                        ip += 2 if ((r0 + r2) % 2 == 1) else 1
                    elif op == {op_mul_acc.lower()!r}:
                        r2 = (r2 + r0 * r1 + arg) % {mod_p}; ip += 1
                return [r0, r1, r2]
        """),
        "verify": lambda resp, h=vm_harness: run_python_tests(extract_code(resp), h),
    })

    # 2. code_sched_penalty: weighted interval selection with 2-slot cap + same-tag overlap penalty + cooldown
    tag_pen = rng.randint(5, 11)
    cool_gap = rng.randint(2, 4)
    prio_thr = rng.randint(6, 9)
    sched_prompt = textwrap.dedent(f"""\
        Write a Python function `max_schedule_score(jobs)` where `jobs` is a list of tuples `(start, end, score, tag, priority)` with `0 <= start < end` (half-open intervals `[start, end)`).
        A valid subset of jobs must satisfy ALL of:
        1. At most 2 chosen jobs may be active simultaneously at any integer time `t` (i.e. `start <= t < end`).
        2. If two chosen jobs have `priority >= {prio_thr}`, their intervals must have a cooldown gap of at least `{cool_gap}` time units between them (if `end_a <= start_b`, then `start_b - end_a >= {cool_gap}`; they cannot overlap at all).
        Total score = sum of `score` of chosen jobs, MINUS `{tag_pen}` for every unordered pair of chosen jobs that overlap (`max(s1, s2) < min(e1, e2)`) AND share the exact same `tag`.
        Return the maximum achievable integer total score (choosing 0 jobs gives `0`). `len(jobs) <= 12`.
        Output only Python code in a ```python block.""")

    def ref_sched(jobs, _pen=tag_pen, _gap=cool_gap, _thr=prio_thr):
        n = len(jobs)
        best = 0
        for mask in range(1 << n):
            sub = [jobs[i] for i in range(n) if (mask >> i) & 1]
            # check max 2 concurrency
            pts = []
            for s, e, sc, tg, pr in sub:
                pts.append((s, 1))
                pts.append((e, -1))
            pts.sort()
            cur = 0
            ok = True
            for _, d in pts:
                cur += d
                if cur > 2:
                    ok = False
                    break
            if not ok:
                continue
            # check high priority cooldown
            hi = sorted([(s, e) for s, e, _, _, pr in sub if pr >= _thr])
            for i in range(len(hi) - 1):
                if hi[i][1] + _gap > hi[i + 1][0]:
                    ok = False
                    break
            if not ok:
                continue
            total = sum(x[2] for x in sub)
            for i in range(len(sub)):
                for j in range(i + 1, len(sub)):
                    if max(sub[i][0], sub[j][0]) < min(sub[i][1], sub[j][1]) and sub[i][3] == sub[j][3]:
                        total -= _pen
            if total > best:
                best = total
        return best

    sched_cases = []
    for _ in range(6):
        jobs = []
        for _j in range(rng.randint(7, 10)):
            s = rng.randint(0, 12)
            e = s + rng.randint(2, 6)
            jobs.append((s, e, rng.randint(4, 20), rng.choice(["alpha", "beta", "gamma"]), rng.randint(1, 10)))
        sched_cases.append((jobs, ref_sched(jobs)))
    sched_harness = f"""
cases = {repr(sched_cases)}
for jobs, exp in cases:
    got = max_schedule_score(list(jobs))
    assert got == exp, f"expected {{exp}}, got {{got}}"
"""
    tasks.append({
        "id": "code_sched_penalty",
        "category": "coding",
        "prompt": sched_prompt,
        "ref_code": textwrap.dedent(f"""\
            def max_schedule_score(jobs):
                n = len(jobs)
                best = 0
                for mask in range(1 << n):
                    sub = [jobs[i] for i in range(n) if (mask >> i) & 1]
                    pts = []
                    for s, e, sc, tg, pr in sub:
                        pts.append((s, 1)); pts.append((e, -1))
                    pts.sort()
                    cur = 0; ok = True
                    for _, d in pts:
                        cur += d
                        if cur > 2: ok = False; break
                    if not ok: continue
                    hi = sorted([(s, e) for s, e, _, _, pr in sub if pr >= {prio_thr}])
                    for i in range(len(hi) - 1):
                        if hi[i][1] + {cool_gap} > hi[i + 1][0]: ok = False; break
                    if not ok: continue
                    total = sum(x[2] for x in sub)
                    for i in range(len(sub)):
                        for j in range(i + 1, len(sub)):
                            if max(sub[i][0], sub[j][0]) < min(sub[i][1], sub[j][1]) and sub[i][3] == sub[j][3]:
                                total -= {tag_pen}
                    if total > best: best = total
                return best
        """),
        "verify": lambda resp, h=sched_harness: run_python_tests(extract_code(resp), h),
    })

    # 3. code_log_compact: out-of-order log compaction with ALIAS + TOMBSTONE + salted tie-break
    del_kw = rng.choice(["TOMBSTONE", "REVOKE", "DROP_KEY"])
    alias_kw = rng.choice(["ALIAS", "REMAP", "RENAME"])
    add_kw = rng.choice(["DELTA", "ADJUST", "INCR"])
    set_kw = rng.choice(["SET", "ASSIGN", "PUT"])
    log_prompt = textwrap.dedent(f"""\
        Write a Python function `compact_events(lines)` that takes a list of event strings formatted as `<ts:int>|<OP>|<key>|<arg>` (with arbitrary surrounding whitespace and occasional blank or `#` comment lines).
        Events may arrive OUT OF TIMESTAMP ORDER. Process events in ascending `(ts, input_index)` order (stable on equal `ts`), applying:
        - Keys are case-insensitive (normalize all keys and alias targets to lowercase).
        - If an `{alias_kw}` event (`<ts>|{alias_kw}|<old_key>|<new_key>`) occurs, any existing state at `<old_key>` moves to `<new_key>` (overwriting `<new_key>` if `<old_key>` existed, or leaving `<new_key>` unchanged if `<old_key>` had no value), `<old_key>` is removed, and ALL future events referencing `<old_key>` resolve transitively through active aliases to its current target.
        - `{set_kw}` (`<ts>|{set_kw}|<key>|<int_val>`): sets key's integer value to `int(arg)`.
        - `{add_kw}` (`<ts>|{add_kw}|<key>|<int_delta>`): adds `int(arg)` to key's current value (starting from `0` if absent).
        - `{del_kw}` (`<ts>|{del_kw}|<key>|_`): deletes the resolved key if present.
        Return a list of `(key, value)` tuples for all surviving keys sorted by **descending `value`**, breaking ties by **ascending `key`**.
        Output only Python code in a ```python block.""")

    def ref_compact(lines):
        parsed = []
        for idx, raw in enumerate(lines):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            ts_s, op, k, arg = [p.strip() for p in s.split("|", 3)]
            parsed.append((int(ts_s), idx, op.upper(), k.lower(), arg.strip()))
        parsed.sort(key=lambda x: (x[0], x[1]))
        aliases = {}
        state = {}

        def res(k):
            seen = set()
            while k in aliases and k not in seen:
                seen.add(k)
                k = aliases[k]
            return k

        for _, _, op, k, arg in parsed:
            if op == alias_kw:
                nk = res(arg.lower())
                ok = res(k)
                if ok != nk:
                    if ok in state:
                        state[nk] = state.pop(ok)
                    aliases[ok] = nk
            elif op == set_kw:
                state[res(k)] = int(arg)
            elif op == add_kw:
                rk = res(k)
                state[rk] = state.get(rk, 0) + int(arg)
            elif op == del_kw:
                state.pop(res(k), None)
        return sorted(state.items(), key=lambda kv: (-kv[1], kv[0]))

    log_cases = []
    keys_pool = ["ItemA", "itemb", "Svc_C", "NODE_X", "tag_Q"]
    for _ in range(5):
        ev = ["# header", ""]
        for _i in range(14):
            ts = rng.randint(1, 20)
            op = rng.choice([set_kw, add_kw, add_kw, del_kw, alias_kw])
            k1 = rng.choice(keys_pool)
            if op == alias_kw:
                k2 = rng.choice([x for x in keys_pool if x.lower() != k1.lower()])
                ev.append(f" {ts} | {op} | {k1} | {k2} ")
            elif op == del_kw:
                ev.append(f"{ts}|{op}|{k1}|_")
            else:
                ev.append(f"{ts}|{op}|{k1}|{rng.randint(-15, 30)}")
        log_cases.append((ev, ref_compact(ev)))
    log_harness = f"""
cases = {repr(log_cases)}
for ev, exp in cases:
    got = compact_events(list(ev))
    assert got == exp, f"expected {{exp}}, got {{got}}"
"""
    tasks.append({
        "id": "code_log_compact",
        "category": "coding",
        "prompt": log_prompt,
        "ref_code": textwrap.dedent(f"""\
            def compact_events(lines):
                parsed = []
                for idx, raw in enumerate(lines):
                    s = raw.strip()
                    if not s or s.startswith("#"): continue
                    ts_s, op, k, arg = [p.strip() for p in s.split("|", 3)]
                    parsed.append((int(ts_s), idx, op.upper(), k.lower(), arg.strip()))
                parsed.sort(key=lambda x: (x[0], x[1]))
                aliases = {{}}
                state = {{}}
                def res(k):
                    seen = set()
                    while k in aliases and k not in seen:
                        seen.add(k); k = aliases[k]
                    return k
                for _, _, op, k, arg in parsed:
                    if op == {alias_kw!r}:
                        nk = res(arg.lower()); ok = res(k)
                        if ok != nk:
                            if ok in state: state[nk] = state.pop(ok)
                            aliases[ok] = nk
                    elif op == {set_kw!r}: state[res(k)] = int(arg)
                    elif op == {add_kw!r}: rk = res(k); state[rk] = state.get(rk, 0) + int(arg)
                    elif op == {del_kw!r}: state.pop(res(k), None)
                return sorted(state.items(), key=lambda kv: (-kv[1], kv[0]))
        """),
        "verify": lambda resp, h=log_harness: run_python_tests(extract_code(resp), h),
    })

    # 4. code_bug_hunt: 3 subtle planted bugs in a sliding-window quota tracker
    burst_mult = rng.choice([2, 3])
    bug_prompt = textwrap.dedent(f"""\
        The Python class `WindowQuota` below is supposed to implement:
        1. Per-key sliding time window of length `window_sec` (an event at timestamp `t` expires when `now - t >= window_sec`).
        2. Standard limit is `max_hits` hits per window, EXCEPT if `burst=True`, allow up to `max_hits * {burst_mult}` hits.
        3. `allow(key, now, burst=False)` records the hit and returns `True` iff adding 1 hit does not exceed the effective limit; if denied, do NOT record the hit.
        4. `active_keys()` returns the sorted list of keys that still had at least one non-expired hit as of the most recent `allow` timestamp (or `purge(now)`).
        There are **3 subtle bugs** in the implementation below. Fix them and output the complete corrected `WindowQuota` class in a ```python block.

        ```python
        class WindowQuota:
            def __init__(self, window_sec, max_hits, buckets={{}}):
                self.window_sec = window_sec
                self.max_hits = max_hits
                self.buckets = buckets
                self.last_now = 0

            def purge(self, now):
                self.last_now = now
                for k in list(self.buckets):
                    self.buckets[k] = [t for t in self.buckets[k] if now - t <= self.window_sec]
                    if not self.buckets[k]:
                        del self.buckets[k]

            def allow(self, key, now, burst=False):
                self.purge(now)
                hits = self.buckets.get(key, [])
                limit = self.max_hits * {burst_mult} if burst else self.max_hits
                if len(hits) > limit:
                    return False
                hits.append(now)
                self.buckets[key] = hits
                return True

            def active_keys(self):
                return sorted(self.buckets.keys())
        ```""")
    bug_harness = f"""
q1 = WindowQuota(10, 2)
q2 = WindowQuota(10, 2)
assert q1.allow("a", 0) is True
assert q1.allow("a", 5) is True
assert q1.allow("a", 9) is False, "3rd hit inside window must fail"
assert q2.active_keys() == [], "mutable default buckets leaked across instances"
assert q1.allow("a", 10) is True, "hit at t=0 must expire at now=10 (now - t >= 10)"
assert q1.allow("a", 12, burst=True) is True
assert q1.active_keys() == ["a"]
q1.purge(22)
assert q1.active_keys() == [], "all hits expired at t=22"
"""
    tasks.append({
        "id": "code_bug_hunt",
        "category": "coding",
        "prompt": bug_prompt,
        "ref_code": textwrap.dedent(f"""\
            class WindowQuota:
                def __init__(self, window_sec, max_hits, buckets=None):
                    self.window_sec = window_sec
                    self.max_hits = max_hits
                    self.buckets = dict(buckets) if buckets is not None else {{}}
                    self.last_now = 0
                def purge(self, now):
                    self.last_now = now
                    for k in list(self.buckets):
                        self.buckets[k] = [t for t in self.buckets[k] if now - t < self.window_sec]
                        if not self.buckets[k]:
                            del self.buckets[k]
                def allow(self, key, now, burst=False):
                    self.purge(now)
                    hits = self.buckets.get(key, [])
                    limit = self.max_hits * {burst_mult} if burst else self.max_hits
                    if len(hits) >= limit:
                        return False
                    hits.append(now)
                    self.buckets[key] = hits
                    return True
                def active_keys(self):
                    return sorted(self.buckets.keys())
        """),
        "verify": lambda resp, h=bug_harness: run_python_tests(extract_code(resp), h),
    })

    # ------------------------------------------------------------------
    # CAT 2: SEEDED MULTI-STEP SYMBOLIC & ALGORITHMIC REASONING (4 tasks)
    # ------------------------------------------------------------------

    # 5. reason_state_chain: 8-step deterministic string+int state machine
    s_init = "".join(rng.choices("ABCDEFGHJKLMNPQR", k=6))
    acc_init = rng.randint(11, 49)
    xor_c = rng.randint(17, 63)
    mod_c = rng.choice([73, 83, 97])
    steps_desc = []
    cur_s, cur_a = s_init, acc_init
    for st in range(1, 9):
        if st % 3 == 1:
            ch = rng.choice(["X", "Z", "Q"])
            steps_desc.append(f"Step {st}: If `len(s)` is even, prepend `{ch}` to `s` and set `acc = (acc + {st*3}) % {mod_c}`; else append `{ch}` and set `acc = (acc ^ {xor_c}) % {mod_c}`.")
            if len(cur_s) % 2 == 0:
                cur_s = ch + cur_s
                cur_a = (cur_a + st * 3) % mod_c
            else:
                cur_s = cur_s + ch
                cur_a = (cur_a ^ xor_c) % mod_c
        elif st % 3 == 2:
            k_rot = rng.choice([1, 2, 3])
            steps_desc.append(f"Step {st}: Rotate `s` left by `{k_rot}` character(s). Then if `acc % 2 == 1`, reverse `s` and set `acc = (acc * 2 + 1) % {mod_c}`; else leave `s` unreversed and set `acc = (acc + len(s)) % {mod_c}`.")
            k_eff = k_rot % len(cur_s)
            cur_s = cur_s[k_eff:] + cur_s[:k_eff]
            if cur_a % 2 == 1:
                cur_s = cur_s[::-1]
                cur_a = (cur_a * 2 + 1) % mod_c
            else:
                cur_a = (cur_a + len(cur_s)) % mod_c
        else:
            steps_desc.append(f"Step {st}: Drop the middle character at index `len(s) // 2` from `s`, and set `acc = (acc + ord(dropped_char)) % {mod_c}`.")
            mid = len(cur_s) // 2
            ch = cur_s[mid]
            cur_s = cur_s[:mid] + cur_s[mid + 1:]
            cur_a = (cur_a + ord(ch)) % mod_c
    chain_ans = f"{cur_s}:{cur_a}"
    chain_prompt = (
        f"Trace this exact state machine starting from `s = {s_init!r}` and `acc = {acc_init}`:\n"
        + "\n".join(steps_desc)
        + f"\nReturn ONLY the final answer formatted strictly as `FINAL: <s_str>:<acc_int>` (for example `FINAL: ABC:42`)."
    )
    tasks.append({
        "id": "reason_state_chain",
        "category": "reasoning",
        "prompt": chain_prompt,
        "ref_answer": chain_ans,
        "verify": lambda resp, exp=chain_ans: (
            (exp in resp.replace("`", "") and re.search(rf"FINAL:\s*{re.escape(exp)}\b", resp.replace("`", "")) is not None),
            f"expected FINAL: {exp}"
        ),
    })

    # 6. reason_graph_constraints: shortest path with no consecutive blue edges & every 3rd step doubles cost
    nodes = ["N0", "N1", "N2", "N3", "N4", "N5", "N6"]
    edges = []
    # Guarantee connectivity + rich alternatives
    base_edges = [
        ("N0", "N1"), ("N0", "N2"), ("N1", "N2"), ("N1", "N3"), ("N1", "N4"),
        ("N2", "N4"), ("N2", "N5"), ("N3", "N5"), ("N3", "N6"), ("N4", "N3"),
        ("N4", "N6"), ("N5", "N6"), ("N5", "N4"),
    ]
    colors = ["red", "blue", "green"]
    adj = {u: [] for u in nodes}
    for u, v in base_edges:
        w = rng.randint(3, 16)
        c = rng.choice(colors)
        edges.append((u, v, w, c))
        adj[u].append((v, w, c))

    # Exact Dijkstra on (node, prev_was_blue, step_mod_3)
    pq = [(0, "N0", False, 0)]
    dist = {}
    ans_cost = None
    while pq:
        d, u, pb, sm = heapq.heappop(pq)
        st = (u, pb, sm)
        if st in dist:
            continue
        dist[st] = d
        if u == "N6":
            ans_cost = d
            break
        for v, w, c in adj[u]:
            if pb and c == "blue":
                continue
            nsm = (sm + 1) % 3
            step_cost = w * 2 if nsm == 0 else w
            nst = (v, c == "blue", nsm)
            if nst not in dist:
                heapq.heappush(pq, (d + step_cost, v, c == "blue", nsm))

    edge_lines = "\n".join(f"- {u} -> {v} (base_cost={w}, color={c})" for u, v, w, c in edges)
    graph_prompt = textwrap.dedent(f"""\
        Find the minimum total cost to travel from `N0` to `N6` in this directed graph:
        {edge_lines}
        Rules:
        1. You may NEVER take two `blue` edges consecutively (taking `blue` then `red`/`green` then `blue` is allowed).
        2. Every **3rd** edge taken along your path (edge #3, #6, #9, ...) costs **2× its `base_cost`**. All other edges cost `1× base_cost`.
        Return ONLY the minimum integer cost formatted strictly as `FINAL: <int>`.""")
    tasks.append({
        "id": "reason_graph_constraints",
        "category": "reasoning",
        "prompt": graph_prompt,
        "ref_answer": str(ans_cost),
        "verify": lambda resp, exp=str(ans_cost): (
            re.search(rf"FINAL:\s*{exp}\b", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    # 7. reason_rule_eval: priority policy resolution with UNLESS exceptions
    th_a = rng.randint(40, 70)
    th_b = rng.randint(10, 30)
    queries = [
        {"tier": "gold", "load": th_a + 5, "err": th_b - 2, "tag": "batch"},
        {"tier": "silver", "load": th_a - 10, "err": th_b + 5, "tag": "live"},
        {"tier": "gold", "load": th_a + 12, "err": th_b + 8, "tag": "live"},
        {"tier": "bronze", "load": th_a - 25, "err": th_b - 5, "tag": "batch"},
    ]

    def eval_pol(q):
        # Evaluated in strict order; first matching rule wins unless UNLESS triggers
        if q["err"] > th_b and not (q["tier"] == "gold" and q["tag"] == "live"):
            return "THROTTLE"
        if q["load"] > th_a and q["tag"] != "batch":
            return "SHED"
        if q["tier"] == "gold" or (q["load"] <= th_a - 15 and q["err"] <= th_b):
            return "ALLOW_FAST"
        return "ALLOW_STD"

    pol_exp = ",".join(eval_pol(q) for q in queries)
    pol_prompt = textwrap.dedent(f"""\
        Evaluate the following 4 policy rules **in strict top-to-bottom order** (the FIRST rule whose condition holds AND whose UNLESS clause does NOT trigger determines the verdict):
        1. `THROTTLE`: if `err > {th_b}` UNLESS `(tier == "gold" AND tag == "live")`
        2. `SHED`: if `load > {th_a}` UNLESS `tag == "batch"`
        3. `ALLOW_FAST`: if `tier == "gold"` OR `(load <= {th_a - 15} AND err <= {th_b})`
        4. `ALLOW_STD`: fallback (always matches)

        Evaluate for:
        Q1: {queries[0]}
        Q2: {queries[1]}
        Q3: {queries[2]}
        Q4: {queries[3]}
        Return ONLY `FINAL: V1,V2,V3,V4` (no spaces after commas).""")
    tasks.append({
        "id": "reason_rule_eval",
        "category": "reasoning",
        "prompt": pol_prompt,
        "ref_answer": pol_exp,
        "verify": lambda resp, exp=pol_exp: (
            re.search(rf"FINAL:\s*{re.escape(exp)}\b", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    # 8. reason_base_checksum: salted radix-13 conversion + weighted checksum
    alpha = list("KMPQRTWXY2579")
    rng.shuffle(alpha)
    alpha_s = "".join(alpha)
    base_n = len(alpha_s)  # 13
    target_val = rng.randint(3500, 28000)
    weights = [rng.randint(2, 7) for _ in range(5)]

    def to_salted(val):
        n = val
        digits = []
        while n > 0:
            digits.append(n % base_n)
            n //= base_n
        digits.reverse()
        s = "".join(alpha_s[d] for d in digits)
        csum = sum(d * weights[i % len(weights)] for i, d in enumerate(digits)) % base_n
        return s + alpha_s[csum]

    chk_exp = to_salted(target_val)
    chk_prompt = textwrap.dedent(f"""\
        Given the custom base-{base_n} digit alphabet (index 0 to {base_n-1}): `{alpha_s}`
        1. Convert the decimal integer `{target_val}` into base-{base_n} using that alphabet (most significant digit first).
        2. Let the digit indices (in `0..{base_n-1}`, left-to-right from index 0) be `d_0, d_1, ...`. Compute a check digit index:
           `c = (sum over i of d_i * W[i % {len(weights)}]) % {base_n}` where `W = {weights}`.
        3. Append the character for index `c` to the end of the base-{base_n} string.
        Return ONLY `FINAL: <string>`.""")
    tasks.append({
        "id": "reason_base_checksum",
        "category": "reasoning",
        "prompt": chk_prompt,
        "ref_answer": chk_exp,
        "verify": lambda resp, exp=chk_exp: (
            re.search(rf"FINAL:\s*{re.escape(exp)}\b", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    # ------------------------------------------------------------------
    # CAT 3: STRICT STRUCTURED OUTPUT & NEGATIVE CONSTRAINTS (4 tasks)
    # ------------------------------------------------------------------

    # 9. struct_json_reconcile: raw minified JSON, no fences, __unset__ + drop <=0
    k_names = [f"fld_{rng.randint(10,99)}_{c}" for c in ("a", "b", "c", "d", "e")]
    base_cfg = {k: rng.randint(5, 50) for k in k_names[:4]}
    ov_cfg = {k_names[1]: 0, k_names[3]: rng.randint(-10, -1), k_names[4]: rng.randint(12, 40)}
    hot_cfg = {"__unset__": [k_names[0]], k_names[3]: rng.randint(8, 25)}
    merged = dict(base_cfg)
    merged.update(ov_cfg)
    for uk in hot_cfg["__unset__"]:
        merged.pop(uk, None)
    for k, v in hot_cfg.items():
        if k != "__unset__":
            merged[k] = v
    exp_cfg = {k: v for k, v in sorted(merged.items()) if v > 0}
    json_prompt = textwrap.dedent(f"""\
        Merge these three configuration layers in order (`base` -> `override` -> `hotfix`):
        - `base`: {json.dumps(base_cfg)}
        - `override`: {json.dumps(ov_cfg)}
        - `hotfix`: {json.dumps(hot_cfg)}
        Rules:
        1. Any key listed in `"__unset__"` inside a layer removes that key from the accumulated config (and `"__unset__"` itself must NOT appear in the final output).
        2. After merging all layers, DROP every key whose final numeric value is `<= 0`.
        3. Output ONLY a single valid raw minified JSON object with keys sorted alphabetically. Do NOT include markdown code fences (no ```), explanations, or extra text.""")

    def verify_raw_json(resp, exp=exp_cfg):
        obj, is_raw = extract_json_obj(resp)
        if obj is None:
            return False, "invalid JSON"
        if not is_raw:
            return False, "violated raw JSON constraint (fences or prose)"
        if list(obj.keys()) != sorted(exp.keys()):
            return False, f"key order/set mismatch: {list(obj.keys())} vs {sorted(exp.keys())}"
        if obj != exp:
            return False, f"value mismatch: {obj} vs {exp}"
        return True, "ok"

    tasks.append({
        "id": "struct_json_reconcile",
        "category": "structured",
        "prompt": json_prompt,
        "ref_answer": json.dumps(exp_cfg, separators=(",", ":")),
        "verify": verify_raw_json,
    })

    # 10. struct_tool_dispatch: extract real tool calls & skip rejected decoys
    port_ok = rng.randint(9100, 9400)
    port_bad = rng.randint(9500, 9800)
    svc_id = f"svc-{rng.randint(100,999)}"
    snap_id = f"snap_{rng.randint(1000,9999)}"
    tool_prompt = textwrap.dedent(f"""\
        Read the ops note below and return ONLY a JSON array of tool calls to execute, matching schema:
        `[{{"tool": "<string>", "args": {{...}}}}]`
        Available tools:
        - `drain_worker(id: str, force: bool)`
        - `verify_snapshot(snapshot_id: str, port: int)`
        - `purge_cache(scope: str)`  (ONLY if explicitly approved)

        Ops Note:
        "Earlier someone suggested running `purge_cache(scope='global')`, DO NOT run that—approval was denied. First call `drain_worker` on `{svc_id}` without forcing (`force=false`). Someone wrote port `{port_bad}` on the sticky note, ignore that old port; verify snapshot `{snap_id}` against port `{port_ok}`."

        Return ONLY raw JSON (no markdown fences, no commentary).""")
    tool_exp = [
        {"tool": "drain_worker", "args": {"id": svc_id, "force": False}},
        {"tool": "verify_snapshot", "args": {"snapshot_id": snap_id, "port": port_ok}},
    ]

    def verify_tools(resp, exp=tool_exp):
        obj, is_raw = extract_json_obj(resp)
        if obj is None:
            return False, "invalid JSON"
        if not is_raw:
            return False, "not raw JSON"
        if obj != exp:
            return False, f"got {obj}, expected {exp}"
        return True, "ok"

    tasks.append({
        "id": "struct_tool_dispatch",
        "category": "structured",
        "prompt": tool_prompt,
        "ref_answer": json.dumps(tool_exp),
        "verify": verify_tools,
    })

    # 11. struct_table_extract: unit normalization + VOID/dup filtering -> JSON aggregate
    rows = []
    # Fixed deterministic 12 lines with 2 VOIDs, 1 duplicate ID (keep later ts), mixed KiB/MiB & ms/s
    valid_map = {}
    used_ids = [f"R{100 + i}" for i in range(10)]
    for i in range(10):
        rid = used_ids[i]
        ts = 10 + i * 2
        sz_mib = rng.randint(2, 20)
        lat_ms = rng.randint(150, 950)
        is_void = i in (2, 7)
        if i % 2 == 0:
            sz_str = f"{sz_mib * 1024} KiB"
            lat_str = f"{lat_ms} ms"
        else:
            sz_str = f"{sz_mib} MiB"
            lat_str = f"{lat_ms / 1000:.3f} s"
        status = "VOID" if is_void else "OK"
        rows.append(f"{ts} | {rid} | {sz_str} | {lat_str} | {status}")
        if not is_void:
            valid_map[rid] = (ts, sz_mib, lat_ms)
    # Add an updated row for R101 at higher ts
    new_mib = rng.randint(25, 40)
    new_ms = rng.randint(1100, 2200)
    rows.append(f"99 | {used_ids[1]} | {new_mib} MiB | {new_ms / 1000:.3f} s | OK")
    valid_map[used_ids[1]] = (99, new_mib, new_ms)
    rng.shuffle(rows)

    exp_agg = {
        "count": len(valid_map),
        "total_mib": sum(v[1] for v in valid_map.values()),
        "max_ms": max(v[2] for v in valid_map.values()),
    }
    tbl_prompt = (
        "Aggregate the log rows below (`ts | id | size | latency | status`).\n"
        "Rules:\n"
        "- Exclude any row whose final status is `VOID`.\n"
        "- If an `id` appears multiple times, keep ONLY the row with the largest `ts`.\n"
        "- Convert all sizes to integer `MiB` (`1 MiB = 1024 KiB`) and latencies to integer `ms` (`1 s = 1000 ms`).\n"
        "Output ONLY a JSON object with exact keys `{\"count\": int, \"total_mib\": int, \"max_ms\": int}` (no fences, no prose):\n\n"
        + "\n".join(rows)
    )
    tasks.append({
        "id": "struct_table_extract",
        "category": "structured",
        "prompt": tbl_prompt,
        "ref_answer": json.dumps(exp_agg),
        "verify": verify_raw_json if False else (lambda resp, e=exp_agg: (
            (lambda o, raw: (o == e and raw, f"raw={raw} got={o} exp={e}"))(*extract_json_obj(resp))
        )),
    })

    # 12. struct_negative_constraints: 4 lines, <=12 words each, ZERO digits, banned words
    banned = ["system", "data", "node", "fast", "error"]
    neg_prompt = textwrap.dedent(f"""\
        Read this brief technical description and summarize its four main stages:
        "Stage one validates incoming packets against a cryptographic signature before queuing them. Stage two splits oversized payloads into fixed chunks and compresses each fragment with LZ4. Stage three routes compressed fragments across redundant storage shards while tracking checksums in a ledger. Stage four verifies quorum acknowledgments from remote replicas before returning a commit receipt."

        Strict formatting rules (ALL must hold):
        1. Output **exactly 4 lines**, numbered `1. `, `2. `, `3. `, `4. `.
        2. Each line (including the number prefix? Wait: EXCLUDING `1. ` etc. — i.e., the words after `1. `) must have **between 4 and 11 words**.
        3. The entire response must contain **ZERO digits `0-9` outside the leading `1.`, `2.`, `3.`, `4.` line prefixes**.
        4. NEVER use any of these banned words (case-insensitive, substring or exact): `{", ".join(banned)}`.
        Output only the 4 numbered lines.""")

    def verify_neg(resp):
        lines = [ln.strip() for ln in resp.strip().splitlines() if ln.strip()]
        if len(lines) != 4:
            return False, f"expected 4 lines, got {len(lines)}"
        for i, ln in enumerate(lines, 1):
            if not ln.startswith(f"{i}. "):
                return False, f"line {i} missing prefix '{i}. ': {ln}"
            body = ln[3:].strip()
            if re.search(r"[0-9]", body):
                return False, f"line {i} contains digit: {body}"
            words = body.split()
            if not (4 <= len(words) <= 11):
                return False, f"line {i} word count {len(words)} outside [4, 11]: {body}"
            low = body.lower()
            for bw in banned:
                if bw in low:
                    return False, f"line {i} used banned word '{bw}'"
        return True, "ok"

    tasks.append({
        "id": "struct_negative_constraints",
        "category": "structured",
        "prompt": neg_prompt,
        "ref_answer": "4 lines, 4-11 words, no digits, no banned words",
        "verify": verify_neg,
    })

    # ------------------------------------------------------------------
    # CAT 4: SALTED LONG-CONTEXT MULTI-HOP & STATE TRACKING (4 tasks)
    # ------------------------------------------------------------------

    # 13. ctx_4k_multihop (~4K tokens)
    hop_lines = []
    # Generate ~170 decoy records + 3 chained hops
    target_req = f"REQ_{rng.randint(7000,7999)}"
    worker_id = f"WRK_{rng.randint(300,399)}"
    shard_id = f"SHD_{rng.randint(50,99)}"
    secret_code = f"OK_{rng.randint(4100,4999)}"
    pos = sorted(rng.sample(range(10, 160), 4))
    for i in range(170):
        if i == pos[0]:
            hop_lines.append(f"[{i:04d}] ROUTE req={target_req} assigned_worker={worker_id} note=primary")
        elif i == pos[1]:
            hop_lines.append(f"[{i:04d}] BIND worker={worker_id} target_shard={shard_id} state=active")
        elif i == pos[2]:
            hop_lines.append(f"[{i:04d}] SHARD_RESULT shard={shard_id} verdict={secret_code} mode=committed")
        elif i == pos[3]:
            # near-miss decoy for target_req on an ignored shadow worker
            hop_lines.append(f"[{i:04d}] SHADOW_TRACE req={target_req} assigned_worker=WRK_999 note=ignored_dryrun")
        else:
            r = f"REQ_{rng.randint(1000,6999)}"
            w = f"WRK_{rng.randint(400,899)}"
            sh = f"SHD_{rng.randint(10,49)}"
            v = f"ERR_{rng.randint(100,999)}"
            hop_lines.append(f"[{i:04d}] TELEMETRY req={r} worker={w} shard={sh} status={v} latency={rng.randint(5,900)}ms")

    ctx4_prompt = (
        f"Trace `{target_req}` through the following event log. Follow its **primary** `assigned_worker` (ignore `SHADOW_TRACE` dry-runs), find that worker's `target_shard` via `BIND`, and report the shard's `verdict` from `SHARD_RESULT`.\n"
        f"Return ONLY `FINAL: <verdict>`.\n\n" + "\n".join(hop_lines)
    )
    tasks.append({
        "id": "ctx_4k_multihop",
        "category": "long_context",
        "prompt": ctx4_prompt,
        "ref_answer": secret_code,
        "verify": lambda resp, exp=secret_code: (
            re.search(rf"FINAL:\s*{re.escape(exp)}\b", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    # 14. ctx_4k_diff_audit (~4K tokens)
    cfg_keys = [f"param_{i:02d}" for i in range(16)]
    snap_a = {k: rng.randint(10, 99) for k in cfg_keys}
    snap_b = dict(snap_a)
    changed_keys = sorted(rng.sample(cfg_keys, 3))
    for ck in changed_keys:
        snap_b[ck] = snap_a[ck] + rng.randint(1, 20)
    noise_block_1 = "\n".join(f"diag_line_{i}: val={rng.randint(1000,9999)} flag=true cpu={rng.randint(1,99)}" for i in range(100))
    noise_block_2 = "\n".join(f"trace_line_{i}: seq={rng.randint(1000,9999)} ok=1 queue={rng.randint(0,9)}" for i in range(100))
    diff_prompt = textwrap.dedent(f"""\
        Embedded below are two JSON snapshots (`SNAPSHOT_A` and `SNAPSHOT_B`) separated by diagnostic logs.
        Find the keys whose integer values **changed** between `SNAPSHOT_A` and `SNAPSHOT_B`, and return ONLY a raw JSON object mapping each changed key to its **new value in `SNAPSHOT_B`** (keys sorted alphabetically, no fences, no extra text).

        {noise_block_1}
        SNAPSHOT_A = {json.dumps(snap_a)}
        {noise_block_2}
        SNAPSHOT_B = {json.dumps(snap_b)}""")
    diff_exp = {k: snap_b[k] for k in changed_keys}
    tasks.append({
        "id": "ctx_4k_diff_audit",
        "category": "long_context",
        "prompt": diff_prompt,
        "ref_answer": json.dumps(diff_exp),
        "verify": lambda resp, e=diff_exp: (
            (lambda o, raw: (o == e and raw, f"raw={raw} got={o} exp={e}"))(*extract_json_obj(resp))
        ),
    })

    # 15. ctx_16k_state_mutation (~15-16K tokens): 4 tracked keys mutated across ~620 lines of noisy logs
    tracked = [f"ACC_{c}" for c in ("ALPHA", "BETA", "GAMMA", "DELTA")]
    acc_state = {k: rng.randint(10, 30) for k in tracked}
    init_line = "INITIAL_ACCOUNTS: " + ", ".join(f"{k}={acc_state[k]}" for k in tracked)
    mut_indices = sorted(rng.sample(range(5, 620), 12))
    stream16 = [init_line]
    for i in range(630):
        if i in mut_indices:
            op = rng.choice(["ADD", "SUB", "SWAP"])
            if op == "ADD":
                k = rng.choice(tracked)
                d = rng.randint(3, 19)
                acc_state[k] += d
                stream16.append(f"[{i:04d}] MUTATE_COMMIT {k} ADD {d}")
            elif op == "SUB":
                k = rng.choice(tracked)
                d = rng.randint(1, 9)
                acc_state[k] -= d
                stream16.append(f"[{i:04d}] MUTATE_COMMIT {k} SUB {d}")
            else:
                k1, k2 = rng.sample(tracked, 2)
                acc_state[k1], acc_state[k2] = acc_state[k2], acc_state[k1]
                stream16.append(f"[{i:04d}] MUTATE_COMMIT SWAP {k1} {k2}")
        else:
            stream16.append(
                f"[{i:04d}] HEARTBEAT span={rng.randint(10000,99999)} mem={rng.randint(100,999)}MiB "
                f"rx={rng.randint(10,999)} tx={rng.randint(10,999)} state=steady"
            )
    ctx16_mut_prompt = (
        "Read the full log stream below. Starting from `INITIAL_ACCOUNTS`, apply every `MUTATE_COMMIT` line in chronological order (`HEARTBEAT` lines are noise).\n"
        "Return ONLY raw JSON mapping `ACC_ALPHA`, `ACC_BETA`, `ACC_GAMMA`, `ACC_DELTA` to their final integer values (no fences, no prose).\n\n"
        + "\n".join(stream16)
    )
    tasks.append({
        "id": "ctx_16k_state_mutation",
        "category": "long_context",
        "prompt": ctx16_mut_prompt,
        "ref_answer": json.dumps(acc_state),
        "verify": lambda resp, e=acc_state: (
            (lambda o, raw: (o == e and raw, f"raw={raw} got={o} exp={e}"))(*extract_json_obj(resp))
        ),
    })

    # 16. ctx_16k_needle_contradiction (~16K tokens): 3 policy needles scattered across ~620 lines
    tag_target = f"TAG_{rng.randint(10,99)}"
    cap_1 = rng.randint(50, 90)
    cap_2 = cap_1 + rng.randint(25, 45)
    cap_3 = cap_2 + rng.randint(15, 35)
    n_pos = sorted([rng.randint(30, 120), rng.randint(250, 380), rng.randint(480, 600)])
    lines16 = []
    for i in range(630):
        if i == n_pos[0]:
            lines16.append(f"[{i:04d}] POLICY_MEMO id=M-101: Maximum batch size for all tags is {cap_1}.")
        elif i == n_pos[1]:
            lines16.append(f"[{i:04d}] POLICY_MEMO id=M-204: Supersedes M-101 — maximum batch size is raised to {cap_2} for priority queues.")
        elif i == n_pos[2]:
            lines16.append(f"[{i:04d}] POLICY_MEMO id=M-309: Exception to M-204 — for {tag_target} on priority queues, maximum batch size is {cap_3}.")
        else:
            lines16.append(
                f"[{i:04d}] AUDIT_LOG seq={rng.randint(10000,99999)} host=srv-{rng.randint(10,99)} "
                f"queue=q{rng.randint(1,9)} latency_ms={rng.randint(2,450)} status=OK"
            )
    exp_memo = f"{cap_3}:M-309"
    ctx16_pol_prompt = (
        f"Read the entire audit stream below, find all `POLICY_MEMO` entries, and determine the effective maximum batch size for `{tag_target}` on a **priority** queue, along with the governing memo `id`.\n"
        f"Return ONLY `FINAL: <int>:<memo_id>`.\n\n" + "\n".join(lines16)
    )
    tasks.append({
        "id": "ctx_16k_needle_contradiction",
        "category": "long_context",
        "prompt": ctx16_pol_prompt,
        "ref_answer": exp_memo,
        "verify": lambda resp, exp=exp_memo: (
            re.search(rf"FINAL:\s*{re.escape(exp)}\b", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    return tasks



def build_v2_extra_tasks(seed: int) -> list[dict]:
    rng = random.Random(seed ^ 0x56325F32)
    extra = []

    # 17. agent_tools: tool_search_first (must invoke web_search with salted token, not guess)
    code_name = f"kestrel-{rng.randint(100,999)}-{rng.choice(['vk','rocm','nvfp4'])}"
    tools_search = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search live web documentation and release notes.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a local file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    ]

    def verify_search_first(resp_obj) -> tuple[bool, str]:
        tcs = resp_obj.get("tool_calls") or []
        if not tcs:
            return False, "no tool_calls emitted"
        fn = tcs[0].get("function", {})
        if fn.get("name") != "web_search":
            return False, f"wrong tool {fn.get('name')}"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            return False, "invalid tool JSON args"
        q = str(args.get("query", "")).lower()
        ok = code_name.split("-")[0] in q and len(q.strip()) >= 5
        return ok, f"query={q!r}"

    extra.append({
        "id": "tool_search_first",
        "category": "agent_tools",
        "messages": [{"role": "user", "content": f"What is the latest release changelog for `{code_name}` kernel driver posted yesterday? Answer concisely."}],
        "tools": tools_search,
        "verify_msg": verify_search_first,
        "ref_msg": {"content": "", "tool_calls": [{"function": {"name": "web_search", "arguments": json.dumps({"query": f"{code_name} release changelog"})}}]},
    })

    # 18. agent_tools: tool_honest_failure (two-turn mocked 503 failure -> must not hallucinate numbers)
    node_alias = f"rack-{rng.randint(11,89)}"
    metric_tag = f"mem_bw_{rng.randint(10,99)}"
    fail_msgs = [
        {"role": "user", "content": f"Check live {metric_tag} GB/s and GPU temp on {node_alias} and give exact numbers."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"command": f"probe {node_alias} {metric_tag}"})}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"error": f"HTTP 503: {node_alias} telemetry endpoint unreachable", "exit_code": 53, "data": None})},
    ]

    def verify_honest_failure(resp_obj) -> tuple[bool, str]:
        txt = (resp_obj.get("content") or "").lower()
        has_err = any(k in txt for k in ("503", "unreachable", "failed", "error", "unable", "cannot retrieve", "unavailable", "exit_code"))
        # Reject fabricated numeric metrics like "240.5 gb/s" or "65 c"
        fake_nums = re.findall(r"\b\d+(?:\.\d+)?\s*(?:gb/s|°c|celsius|tok/s|mib)\b", txt)
        ok = has_err and len(fake_nums) == 0
        return ok, f"has_err={has_err} fake_nums={fake_nums}"

    extra.append({
        "id": "tool_honest_failure",
        "category": "agent_tools",
        "messages": fail_msgs,
        "tools": None,
        "verify_msg": verify_honest_failure,
        "ref_msg": {"content": f"The telemetry probe for {node_alias} failed with HTTP 503 (unreachable, exit_code 53), so live {metric_tag} and temperature metrics are unavailable."},
    })

    # 19. agent_tools: tool_surgical_patch (must call patch tool cleanly, not write_file)
    mult = rng.randint(3, 9)
    bias = rng.randint(5, 19)
    buggy_code = (
        "def score_item(x: int, weight: int) -> int:\n"
        f"    # BUG: should multiply by {mult} and add {bias}, currently adds 1 instead of {bias}\n"
        f"    base = x * {mult} + 1\n"
        "    return base * weight\n"
    )
    tools_patch = [
        {
            "type": "function",
            "function": {
                "name": "patch",
                "description": "Targeted find-and-replace edit in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Overwrite entire file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
    ]

    def verify_surgical_patch(resp_obj) -> tuple[bool, str]:
        tcs = resp_obj.get("tool_calls") or []
        if not tcs:
            return False, "no tool_calls emitted"
        fn = tcs[0].get("function", {})
        if fn.get("name") != "patch":
            return False, f"expected patch, got {fn.get('name')}"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            return False, "invalid JSON args"
        old_s = args.get("old_string", "")
        new_s = args.get("new_string", "")
        if not old_s or old_s not in buggy_code or old_s == new_s:
            return False, "old_string did not uniquely match snippet"
        patched = buggy_code.replace(old_s, new_s, 1)
        harness = f"assert score_item(4, 2) == {(4 * mult + bias) * 2}\nassert score_item(0, 5) == {bias * 5}"
        return run_python_tests(patched, harness)

    extra.append({
        "id": "tool_surgical_patch",
        "category": "agent_tools",
        "messages": [{"role": "user", "content": f"Use `patch` (do not use `write_file`) to fix `/tmp/scorer.py`:\n```python\n{buggy_code}```"}],
        "tools": tools_patch,
        "verify_msg": verify_surgical_patch,
        "ref_msg": {"content": "", "tool_calls": [{"function": {"name": "patch", "arguments": json.dumps({"path": "/tmp/scorer.py", "old_string": f"    base = x * {mult} + 1", "new_string": f"    base = x * {mult} + {bias}"})}}]},
    })

    # 20. agent_tools: tool_kanban_triage (strict raw JSON routing decision)
    is_heavy = rng.choice([True, False])
    exp_route = "node2_overlord" if is_heavy else "node1_grunt"
    ticket = (
        f"Refactor Vulkan + CUDA KV-cache quantization and cross-crate ABI across 9 files in src/ and ggml/, re-run integration suite, and coordinate review."
        if is_heavy else
        f"Fix typo in constant K_{rng.randint(10,99)} in one local helper file src/fmt_util.py and run pytest -k fmt_util."
    )

    def verify_triage(resp_obj) -> tuple[bool, str]:
        obj, raw = extract_json_obj(resp_obj.get("content") or "")
        ok = raw and isinstance(obj, dict) and obj.get("route") == exp_route and isinstance(obj.get("reason"), str) and len(obj["reason"]) >= 5
        return ok, f"raw={raw} got={obj} exp={exp_route}"

    extra.append({
        "id": "tool_kanban_triage",
        "category": "agent_tools",
        "messages": [{"role": "user", "content": f"Route this ticket to `node1_grunt` (bounded 1-file edit/test) or `node2_overlord` (multi-file refactor/architecture). Return ONLY raw JSON `{{\"route\": \"...\", \"reason\": \"...\"}}` with no markdown fences.\nTicket: {ticket}"}],
        "tools": None,
        "verify_msg": verify_triage,
        "ref_msg": {"content": json.dumps({"route": exp_route, "reason": "Matches complexity scope."})},
    })

    # 21. ops_and_voice: ops_log_triage_8k
    err_file = f"src/dsp/stage_{rng.randint(10,99)}.rs"
    err_line = rng.randint(105, 890)
    err_code = rng.choice([2, 101, 139])
    err_sym = f"ErrSym_{rng.randint(1000,9999)}"
    log_lines = []
    target_idx = rng.randint(180, 260)
    for i in range(320):
        ts = f"2026-11-22T12:{(i//60)%60:02d}:{i%60:02d}Z"
        if i == target_idx:
            log_lines.append(f"{ts} ERROR compiler::link [{err_file}:{err_line}] undefined reference `{err_sym}` (process exited with code {err_code})")
        elif i % 47 == 0:
            log_lines.append(f"{ts} WARN cargo::lint unused variable `tmp_{i}` in src/util/pad_{i}.rs:{i+10} (non-fatal)")
        else:
            log_lines.append(f"{ts} INFO build::step compiling crate mod_{i:03d} object hash={hashlib.md5(f'{seed}:{i}'.encode()).hexdigest()[:12]} elapsed_ms={rng.randint(4,88)}")
    exp_log = {"file": err_file, "line": err_line, "exit_code": err_code, "symbol": err_sym}

    def verify_log_triage(resp_obj) -> tuple[bool, str]:
        obj, raw = extract_json_obj(resp_obj.get("content") or "")
        ok = raw and obj == exp_log
        return ok, f"raw={raw} got={obj} exp={exp_log}"

    extra.append({
        "id": "ops_log_triage_8k",
        "category": "ops_and_voice",
        "messages": [{"role": "user", "content": f"Extract the single FATAL ERROR (ignore non-fatal WARN lines) and return ONLY raw JSON with keys `file`, `line` (int), `exit_code` (int), `symbol`.\n\n" + "\n".join(log_lines)}],
        "tools": None,
        "verify_msg": verify_log_triage,
        "ref_msg": {"content": json.dumps(exp_log)},
    })

    # 22. ops_and_voice: ops_rag_mixed_16k (modeled after RAG_mixed_test_265kb.txt)
    final_batch = rng.choice([1024, 2048, 4096, 8192])
    final_thresh = round(rng.uniform(0.45, 0.95), 2)
    final_worker = f"worker-{rng.randint(10,99)}z"
    rag_blocks = []
    inject_b = rng.randint(110, 145)
    for b in range(165):
        bs = final_batch if b == inject_b else rng.choice([128, 256, 512])
        th = final_thresh if b == inject_b else round(rng.uniform(0.11, 0.39), 2)
        wk = final_worker if b == inject_b else f"worker-{rng.randint(10,99)}a"
        flag = "ACTIVE_OVERRIDE" if b == inject_b else "HISTORICAL_SNAPSHOT"
        rag_blocks.append(
            f"--- BLOCK {b:03d} ({flag}) ---\n"
            f"[PROSE]\nDistributed stream buffering regulates pressure when source throughput exceeds channel capacity.\n"
            f"[STRUCTURED JSON LOG]\n{{\"block\": {b}, \"status\": \"{flag}\", \"batch_size\": {bs}, \"worker_id\": \"{wk}\"}}\n"
            f"[SOURCE CODE FUNCTION]\nfunction check_{b}(v) {{ return v >= {th}; }}\n"
            f"[TABULAR DATA RECORD]\nid,threshold,worker\n{1000+b},{th},{wk}\n"
        )
    exp_rag = {"batch_size": final_batch, "threshold": final_thresh, "active_worker": final_worker}

    def verify_rag_mixed(resp_obj) -> tuple[bool, str]:
        obj, raw = extract_json_obj(resp_obj.get("content") or "")
        ok = raw and isinstance(obj, dict) and obj.get("batch_size") == final_batch and abs(float(obj.get("threshold", -1)) - final_thresh) < 1e-6 and obj.get("active_worker") == final_worker
        return ok, f"raw={raw} got={obj} exp={exp_rag}"

    extra.append({
        "id": "ops_rag_mixed_16k",
        "category": "ops_and_voice",
        "messages": [{"role": "user", "content": "Find the single block marked `ACTIVE_OVERRIDE` (ignore `HISTORICAL_SNAPSHOT` blocks) and return ONLY raw JSON `{\"batch_size\": int, \"threshold\": float, \"active_worker\": str}`.\n\n" + "\n".join(rag_blocks)}],
        "tools": None,
        "verify_msg": verify_rag_mixed,
        "ref_msg": {"content": json.dumps(exp_rag)},
    })

    # 23. ops_and_voice: voice_concise_bullets
    topic = rng.choice(["unified memory bandwidth", "FlashAttention KV paging", "speculative draft verification", "MoE expert routing"])

    def verify_bullets(resp_obj) -> tuple[bool, str]:
        txt = (resp_obj.get("content") or "").strip()
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        if len(lines) != 3:
            return False, f"expected 3 lines, got {len(lines)}"
        if not all(re.match(r"^[-*]\s+", l) for l in lines):
            return False, "all 3 lines must start with '- ' or '* '"
        bad = [w for w in ("delve", "leverage", "seamless", "crucial", "tapestry") if re.search(rf"\b{w}\b", txt, re.I)]
        if bad:
            return False, f"forbidden words {bad}"
        counts = [len(re.sub(r"^[-*]\s+", "", l).split()) for l in lines]
        ok = all(1 <= c <= 14 for c in counts)
        return ok, f"word_counts={counts}"

    extra.append({
        "id": "voice_concise_bullets",
        "category": "ops_and_voice",
        "messages": [{"role": "user", "content": f"Explain {topic} in EXACTLY 3 bullet points starting with '- '. Each bullet must have <= 14 words. Never use the words delve, leverage, seamless, or crucial. Output only the 3 bullets."}],
        "tools": None,
        "verify_msg": verify_bullets,
        "ref_msg": {"content": "- Shared memory eliminates redundant copies between CPU and integrated GPU.\n- Memory bandwidth limits how fast decode reads quantized weights.\n- Larger batch sizes reuse loaded weights across many prompt tokens."},
    })

    # 24. ops_and_voice: voice_sustained_spec
    const_a = f"RING-{rng.randint(100,999)}"
    const_b = f"{rng.randint(64,256)} MiB"
    headers = ["## 1. Architecture", "## 2. Memory Budget", "## 3. Failure Modes", "## 4. Verification"]

    def verify_sustained(resp_obj) -> tuple[bool, str]:
        txt = (resp_obj.get("content") or "").strip()
        words = len(txt.split())
        has_h = all(h in txt for h in headers)
        has_c = (const_a in txt) and (const_b in txt)
        ok = words >= 380 and has_h and has_c
        return ok, f"words={words} has_h={has_h} has_c={has_c}"

    extra.append({
        "id": "voice_sustained_spec",
        "category": "ops_and_voice",
        "messages": [{"role": "user", "content": f"Write a detailed engineering design note of at least 420 words for buffer subsystem `{const_a}` with a `{const_b}` cap. Include these exact four section headers verbatim: `## 1. Architecture`, `## 2. Memory Budget`, `## 3. Failure Modes`, `## 4. Verification`."}],
        "tools": None,
        "verify_msg": verify_sustained,
        "ref_msg": {"content": "\n\n".join(f"{h}\n" + (" ".join([f"Subsystem {const_a} enforces a strict {const_b} cap across queues and arenas."] * 15)) for h in headers)},
    })

    return extra


def build_v3_frontier_tasks(seed: int) -> list[dict]:
    rng = random.Random(seed ^ 0x46524F4E)
    frontier = []

    # 25. front_bilevel_dp: Adversarial minimax sequential DP with global switch-budget + retroactive cliff penalty
    sw_cost = rng.randint(4, 9)
    cliff_k = rng.randint(3, 5)
    cliff_pen = rng.randint(18, 30)
    fault_mult = rng.choice([2, 3])
    bilevel_prompt = textwrap.dedent(f"""\
        Write a Python function `minimax_fault_schedule(costs, fault_penalties)` solving an exact adversarial scheduling problem for `len(costs) <= 28`:
        - `costs` is a list of `T` pairs `(c0, c1)` where epoch `t` (`0 <= t < T`) runs in mode `m_t in {{0, 1}}` with base cost `costs[t][m_t]`.
        - `fault_penalties` is a list of `T` pairs `(f0, f1)` representing extra fault penalty if an adversary strikes epoch `t` while in mode `m_t`.
        Given a binary schedule `M = [m_0, ..., m_{{T-1}}]`:
        1. Let `switches` = count of indices `t >= 1` with `m_t != m_{{t-1}}`.
        2. `base_sum(M) = sum(costs[t][M[t]] for t in range(T))`
        3. `sw_total(M) = switches * {sw_cost} + max(0, switches - {cliff_k}) * {cliff_pen}` (every switch above `{cliff_k}` adds a cliff penalty of `{cliff_pen}`).
        4. Adversarial fault surcharge if an adversary strikes single epoch `k in range(T)`:
           * If `M[k] == 0`: `surcharge(M, k) = fault_penalties[k][0]`.
           * If `M[k] == 1`: `surcharge(M, k) = fault_penalties[k][1] * {fault_mult}` PLUS `{sw_cost}` if `k > 0 and M[k] != M[k-1]`.
        5. `worst_case_cost(M) = base_sum(M) + sw_total(M) + max(surcharge(M, k) for k in range(T))` (or `0` if `T == 0`).
        Return the minimum possible integer `worst_case_cost(M)` over ALL `2^T` schedules `M`, running in `< 1 second` for `T = 26` (`O(2^T)` brute force is rejected).
        Output ONLY Python code in a ```python block.""")

    ref_bilevel_code = textwrap.dedent(f"""\
        def minimax_fault_schedule(costs, fault_penalties):
            T = len(costs)
            if T == 0:
                return 0
            # Note: fixed worst-case budget bound B? Better: DP over (t, mode, switches, best_or_exact)
            # Notice max_surcharge across k is equivalent to: fix an upper bound M_cap on allowed surcharge(k)!
            # Since only at most 2*T distinct surcharge values exist, iterate over all candidate caps C!
            cands = set()
            for k in range(T):
                cands.add(fault_penalties[k][0])
                cands.add(fault_penalties[k][1] * {fault_mult})
                cands.add(fault_penalties[k][1] * {fault_mult} + {sw_cost})
            INF = 10**18
            best_total = INF
            max_sw = max(0, T - 1)
            for cap in sorted(cands):
                # Find min (base_sum + sw_total) subject to EVERY k having surcharge(M, k) <= cap
                dp0 = [INF] * (max_sw + 1)
                dp1 = [INF] * (max_sw + 1)
                if fault_penalties[0][0] <= cap:
                    dp0[0] = costs[0][0]
                if fault_penalties[0][1] * {fault_mult} <= cap:
                    dp1[0] = costs[0][1]
                for t in range(1, T):
                    ndp0 = [INF] * (max_sw + 1)
                    ndp1 = [INF] * (max_sw + 1)
                    ok0 = (fault_penalties[t][0] <= cap)
                    ok1_same = (fault_penalties[t][1] * {fault_mult} <= cap)
                    ok1_sw = (fault_penalties[t][1] * {fault_mult} + {sw_cost} <= cap)
                    c0, c1 = costs[t]
                    for s in range(t):
                        v0, v1 = dp0[s], dp1[s]
                        if v0 < INF:
                            if ok0 and v0 + c0 < ndp0[s]:
                                ndp0[s] = v0 + c0
                            if ok1_sw and v0 + c1 < ndp1[s + 1]:
                                ndp1[s + 1] = v0 + c1
                        if v1 < INF:
                            if ok0 and v1 + c0 < ndp0[s + 1]:
                                ndp0[s + 1] = v1 + c0
                            if ok1_same and v1 + c1 < ndp1[s]:
                                ndp1[s] = v1 + c1
                    dp0, dp1 = ndp0, ndp1
                for s in range(max_sw + 1):
                    v = min(dp0[s], dp1[s])
                    if v < INF:
                        sw_pen = s * {sw_cost} + max(0, s - {cliff_k}) * {cliff_pen}
                        if v + sw_pen + cap < best_total:
                            best_total = v + sw_pen + cap
            return best_total
    """)
    ns_bi = {}
    exec(ref_bilevel_code, ns_bi)
    ref_bi_fn = ns_bi["minimax_fault_schedule"]

    # Cross-check small brute-force oracle for T=10 + generate fast T=26 test cases
    bi_cases = []
    for ci in range(4):
        T_len = 9 if ci < 2 else 26
        cs = [(rng.randint(5, 30), rng.randint(5, 30)) for _ in range(T_len)]
        fps = [(rng.randint(8, 45), rng.randint(4, 25)) for _ in range(T_len)]
        ans = ref_bi_fn(cs, fps)
        if ci < 2:
            # Verify against brute-force 2^T on T=9
            bf_best = 10**18
            for mask in range(1 << T_len):
                M = [(mask >> t) & 1 for t in range(T_len)]
                sw = sum(1 for t in range(1, T_len) if M[t] != M[t - 1])
                bs = sum(cs[t][M[t]] for t in range(T_len))
                sp = sw * sw_cost + max(0, sw - cliff_k) * cliff_pen
                worst = max(
                    fps[k][0] if M[k] == 0 else (fps[k][1] * fault_mult + (sw_cost if k > 0 and M[k] != M[k - 1] else 0))
                    for k in range(T_len)
                )
                bf_best = min(bf_best, bs + sp + worst)
            assert ans == bf_best
        bi_cases.append((cs, fps, ans))

    bi_harness = textwrap.dedent(f"""\
        import time
        cases = {repr(bi_cases)}
        t0 = time.monotonic()
        for cs, fps, exp in cases:
            got = minimax_fault_schedule(list(cs), list(fps))
            assert got == exp, f"expected {{exp}}, got {{got}}"
        el = time.monotonic() - t0
        assert el < 2.0, f"too slow ({{el:.2f}}s)"
    """)
    frontier.append({
        "id": "front_bilevel_dp",
        "category": "frontier_ceiling",
        "prompt": bilevel_prompt,
        "ref_code": ref_bilevel_code,
        "verify": lambda resp, h=bi_harness: run_python_tests(extract_code(resp), h),
    })

    # 26. front_nonlocal_bijection_10: unique 10-variable permutation CSP over {1..10} with cycles [2,3,5], LIS==4, modular & parity ties
    def lis_len(seq):
        tails = []
        for x in seq:
            lo, hi = 0, len(tails)
            while lo < hi:
                mid = (lo + hi) // 2
                if tails[mid] < x: lo = mid + 1
                else: hi = mid
            if lo == len(tails): tails.append(x)
            else: tails[lo] = x
        return len(tails)

    def count_10_solutions(conds):
        p1, p2, w1, w2, r1, r2, sum_acegi, sum_bdfhj, xor_aj, diff_ei, c_gt_g, b_par, tgt_lis = conds
        # Backtrack with aggressive modular + parity + partial-sum pruning across all 10! perms (<30ms!)
        used = [False] * 11
        arr = [0] * 10
        sols = 0
        def bt(idx):
            nonlocal sols
            if sols > 1:
                return
            if idx == 10:
                if lis_len(arr) != tgt_lis:
                    return
                # Check cycles [2, 3, 5]
                seen = [False] * 10
                cy = []
                for k in range(10):
                    if not seen[k]:
                        cl = 0; cu = k
                        while not seen[cu]:
                            seen[cu] = True; cu = arr[cu] - 1; cl += 1
                        cy.append(cl)
                if sorted(cy) == [2, 3, 5]:
                    sols += 1
                return
            for v in range(1, 11):
                if used[v]:
                    continue
                if idx == 1 and (v % 2) != b_par:
                    continue
                if idx == 6 and ((arr[2] > v) != c_gt_g):
                    continue
                if idx == 8:
                    # E - I == diff_ei (idx 4, 8)
                    if arr[4] - v != diff_ei:
                        continue
                    # A + C + E + G + I == sum_acegi (idx 0,2,4,6,8)
                    if arr[0] + arr[2] + arr[4] + arr[6] + v != sum_acegi:
                        continue
                    # (w1[0]*A + w1[1]*D + w1[2]*G + w1[3]*I) % p1 == r1 (idx 0,3,6,8)
                    if (w1[0]*arr[0] + w1[1]*arr[3] + w1[2]*arr[6] + w1[3]*v) % p1 != r1:
                        continue
                if idx == 9:
                    if (arr[0] ^ v) != xor_aj:
                        continue
                    if arr[1] + arr[3] + arr[5] + arr[7] + v != sum_bdfhj:
                        continue
                    if (w2[0]*arr[1] + w2[1]*arr[4] + w2[2]*arr[7] + w2[3]*v) % p2 != r2:
                        continue
                used[v] = True; arr[idx] = v
                bt(idx + 1)
                used[v] = False
        bt(0)
        return sols

    while True:
        target10 = list(range(1, 11))
        rng.shuffle(target10)
        seen = [False] * 10
        cycs = []
        for i in range(10):
            if not seen[i]:
                cl = 0; cu = i
                while not seen[cu]:
                    seen[cu] = True; cu = target10[cu] - 1; cl += 1
                cycs.append(cl)
        if sorted(cycs) != [2, 3, 5]:
            continue
        A, B, C, D, E, F, G, H, I, J = target10
        tgt_lis = lis_len(target10)
        if tgt_lis not in (4, 5):
            continue
        p1, p2 = rng.choice([11, 13, 17]), rng.choice([13, 19])
        w1 = [rng.randint(1, 5) for _ in range(4)]
        w2 = [rng.randint(1, 5) for _ in range(4)]
        r1 = (w1[0]*A + w1[1]*D + w1[2]*G + w1[3]*I) % p1
        r2 = (w2[0]*B + w2[1]*E + w2[2]*H + w2[3]*J) % p2
        conds = (p1, p2, w1, w2, r1, r2, A+C+E+G+I, B+D+F+H+J, A^J, E-I, C>G, B%2, tgt_lis)
        if count_10_solutions(conds) == 1:
            break

    bij10_ans = ",".join(str(x) for x in target10)
    bij10_prompt = textwrap.dedent(f"""\
        Let `(A, B, C, D, E, F, G, H, I, J)` be a permutation of `{{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}}` (all 10 distinct).
        View the permutation as a directed map `1->A, 2->B, ..., 10->J`.
        Find the UNIQUE assignment satisfying ALL 10 simultaneous global constraints:
        1. Cycle decomposition of `1..10` consists of **exactly three disjoint cycles of lengths 2, 3, and 5**.
        2. Longest Strictly Increasing Subsequence length of `[A, B, C, D, E, F, G, H, I, J]` is **exactly `{tgt_lis}`**.
        3. `B` is `{'odd' if B % 2 else 'even'}`.
        4. `C {'>' if C > G else '<'} G`.
        5. `E - I = {E - I}`.
        6. `A + C + E + G + I = {A + C + E + G + I}`.
        7. `B + D + F + H + J = {B + D + F + H + J}`.
        8. `A XOR J = {A ^ J}` (bitwise XOR).
        9. `({w1[0]}*A + {w1[1]}*D + {w1[2]}*G + {w1[3]}*I) % {p1} = {r1}`.
        10. `({w2[0]}*B + {w2[1]}*E + {w2[2]}*H + {w2[3]}*J) % {p2} = {r2}`.
        Return ONLY `FINAL: A,B,C,D,E,F,G,H,I,J` (10 comma-separated integers, no spaces).""")
    frontier.append({
        "id": "front_nonlocal_bijection_10",
        "category": "frontier_ceiling",
        "prompt": bij10_prompt,
        "ref_answer": bij10_ans,
        "verify": lambda resp, exp=bij10_ans: (
            re.search(rf"FINAL:\s*{re.escape(exp)}(?:\s|$)", resp.replace("`", "")) is not None,
            f"expected FINAL: {exp}"
        ),
    })

    # 27. front_16k_rollback_audit: ~16k-token global transactional rollback + multi-hop aliases + distant revoke audit
    orig_keys = [f"KEY_{i:02d}" for i in range(10)]
    tier_map = {k: rng.choice(["DEV", "STAGE", "PROD"]) for k in orig_keys}
    base_quota = {k: rng.randint(25, 80) for k in orig_keys}
    alias_pairs = []
    avail_k = list(orig_keys)
    rng.shuffle(avail_k)
    for k in avail_k[:4]:
        alias_pairs.append((k, f"{k}_V2"))
    tx_events = [
        ("QUOTA", rng.choice(orig_keys), rng.randint(15, 110)),
        ("TIER", rng.choice(orig_keys), rng.choice(["DEV", "PROD"])),
        ("EXEMPT", rng.choice(orig_keys), None),  # exempt inside rolled-back TX -> MUST rollback!
    ]
    post_updates = [
        ("QUOTA", rng.choice(orig_keys), rng.randint(15, 120)),
        ("QUOTA", rng.choice(orig_keys), rng.randint(15, 120)),
        ("TIER", rng.choice(orig_keys), rng.choice(["DEV", "PROD"])),
        ("TIER", rng.choice(orig_keys), rng.choice(["DEV", "PROD"])),
    ]
    exempt_perm = rng.sample(orig_keys, 3)
    revoked_perm = rng.sample([k for k in orig_keys if k not in exempt_perm], 2)

    ev_slots = {}
    used_idx = set()
    def place(lo, hi, line):
        while True:
            p = rng.randint(lo, hi)
            if p not in used_idx:
                used_idx.add(p); ev_slots[p] = line; return

    for k in orig_keys:
        place(10, 110, f"BASE_QUOTA key={k} quota={base_quota[k]}")
    for ok_k, nk in alias_pairs[:2]:
        place(120, 220, f"ALIAS_MAP old={ok_k} new={nk}")
    # Checkpointed TX block that gets ROLLED BACK
    place(240, 240, "TX_BEGIN id=TX_99")
    place(260, 310, f"QUOTA_COMMIT key={tx_events[0][1]} quota={tx_events[0][2]}")
    place(330, 380, f"TIER_SHIFT key={tx_events[1][1]} new_tier={tx_events[1][2]}")
    place(400, 440, f"EXEMPT_LEDGER key={tx_events[2][1]} rule=HALF_IF_FINAL_PROD")
    place(460, 460, "TX_ROLLBACK id=TX_99")
    # Post-rollback real events + late aliases + revokes across 820 lines (~16k tok)
    for ok_k, nk in alias_pairs[2:]:
        place(480, 560, f"ALIAS_MAP old={ok_k} new={nk}")
    for kind, k, val in post_updates:
        if kind == "QUOTA":
            place(570, 680, f"QUOTA_COMMIT key={k} quota={val}")
        else:
            place(690, 740, f"TIER_SHIFT key={k} new_tier={val}")
    for k in exempt_perm:
        place(600, 760, f"EXEMPT_LEDGER key={k} rule=HALF_IF_FINAL_PROD")
    for k in revoked_perm:
        place(770, 815, f"CONFLICT_REVOKE key={k} reason=SECURITY_HOLD")

    stream16f = [f"TIER_INIT key={k} tier={tr}" for k, tr in tier_map.items()]
    for i in range(820):
        if i in ev_slots:
            stream16f.append(f"[{i:04d}] {ev_slots[i]}")
        else:
            stream16f.append(
                f"[{i:04d}] METRIC_NOISE shard=s{i%17:02d} key=NOISE_{rng.randint(100,999)} "
                f"lat={rng.randint(4,980)}ms crc=0x{rng.randint(0x1000,0xffff):04x} status=OK"
            )

    # Exact replay with snapshot/rollback semantics
    cur = {"alias": {}, "tier": dict(tier_map), "quota": {}, "exempt": set(), "rev": set()}
    snap = None
    def rk_f(st, name):
        seen = set()
        while name in st["alias"] and name not in seen:
            seen.add(name); name = st["alias"][name]
        return name
    for ln in stream16f:
        if "TX_BEGIN " in ln:
            snap = {"alias": dict(cur["alias"]), "tier": dict(cur["tier"]), "quota": dict(cur["quota"]), "exempt": set(cur["exempt"]), "rev": set(cur["rev"])}
        elif "TX_ROLLBACK " in ln:
            cur = snap; snap = None
        elif "BASE_QUOTA " in ln or "QUOTA_COMMIT " in ln:
            m = re.search(r"key=(\S+) quota=(\d+)", ln)
            cur["quota"][rk_f(cur, m.group(1))] = int(m.group(2))
        elif "ALIAS_MAP " in ln:
            m = re.search(r"old=(\S+) new=(\S+)", ln)
            ok_k, nk = rk_f(cur, m.group(1)), rk_f(cur, m.group(2))
            cur["alias"][ok_k] = nk
            if ok_k in cur["tier"]: cur["tier"][nk] = cur["tier"].pop(ok_k)
            if ok_k in cur["quota"]: cur["quota"][nk] = cur["quota"].pop(ok_k)
            if ok_k in cur["exempt"]: cur["exempt"].discard(ok_k); cur["exempt"].add(nk)
            if ok_k in cur["rev"]: cur["rev"].discard(ok_k); cur["rev"].add(nk)
        elif "EXEMPT_LEDGER " in ln:
            m = re.search(r"key=(\S+)", ln)
            cur["exempt"].add(rk_f(cur, m.group(1)))
        elif "TIER_SHIFT " in ln:
            m = re.search(r"key=(\S+) new_tier=(\S+)", ln)
            cur["tier"][rk_f(cur, m.group(1))] = m.group(2)
        elif "CONFLICT_REVOKE " in ln:
            m = re.search(r"key=(\S+)", ln)
            cur["rev"].add(rk_f(cur, m.group(1)))

    eff = {}
    for k, qv in cur["quota"].items():
        rk = rk_f(cur, k)
        if rk in cur["rev"]:
            continue
        tr = cur["tier"].get(rk, "DEV")
        val = qv * 2 if tr == "PROD" else qv
        if rk in cur["exempt"] and tr == "PROD":
            val = val // 2
        eff[rk] = val
    audit_exp = {
        "checksum": sum(eff.values()),
        "survivor_count": len(eff),
        "over_80": sorted([k for k, v in eff.items() if v > 80]),
    }
    audit_prompt = (
        "Perform a transactional global relational audit across ~820 log lines below. Events execute in strict chronological order:\n"
        "- `TIER_INIT` & `TIER_SHIFT`: track each key's tier (`DEV`, `STAGE`, `PROD`).\n"
        "- `BASE_QUOTA` & `QUOTA_COMMIT`: set a key's raw quota.\n"
        "- `ALIAS_MAP old=A new=B`: migrates A's state (tier, quota, exemptions, revocations) to B and maps A->B for future events.\n"
        "- `EXEMPT_LEDGER key=K rule=HALF_IF_FINAL_PROD`: halves effective quota (`// 2`) ONLY IF K's final tier is `PROD`.\n"
        "- `CONFLICT_REVOKE key=K`: excludes K from surviving keys.\n"
        "- **`TX_BEGIN id=...` / `TX_ROLLBACK id=...`**: ALL events between `TX_BEGIN` and `TX_ROLLBACK` are reverted to the exact state right before `TX_BEGIN`!\n"
        "After all lines, for each non-revoked surviving key: `effective = raw_quota * 2` if final tier is `PROD`, else `raw_quota`; then apply `// 2` if exempt AND final tier is `PROD`.\n"
        "Return ONLY raw JSON (no markdown fences) with exact keys: `{\"checksum\": int, \"survivor_count\": int, \"over_80\": [sorted keys with effective > 80]}`.\n\n"
        + "\n".join(stream16f)
    )
    frontier.append({
        "id": "front_16k_rollback_audit",
        "category": "frontier_ceiling",
        "prompt": audit_prompt,
        "ref_answer": json.dumps(audit_exp),
        "verify": lambda resp, e=audit_exp: (
            (lambda o, raw: (o == e and raw, f"raw={raw} got={o} exp={e}"))(*extract_json_obj(resp))
        ),
    })

    # 28. front_ood_affine_tx_taint: Out-of-distribution affine+entangled+TX machine where TAINT leaks monotonically past TX_ROLLBACK!
    p_dsl = rng.choice([89, 97, 101])
    op_load, op_tload, op_move, op_clone, op_ent, op_split, op_mix, op_seal, op_btx, op_rtx, op_ctx = rng.sample(
        ["ZLOAD", "UTLOAD", "AFFMOVE", "CCOPY", "LINK", "UNLINK", "FUSE", "CERTIFY", "TXSTART", "TXABORT", "TXSAVE", "KPUT"], 11
    )
    dsl_prompt = textwrap.dedent(f"""\
        Write a Python function `analyze_tx_machine(program)` implementing a transactional 8-slot (`0..7`) capability verifier modulo `{p_dsl}`.
        Initially all 8 slots are `UNINIT`. Each initialized slot holds `(val: int, copyable: bool, tainted: bool, partner: int | None)`.
        Additionally, maintain a global persistent bitmask `sticky_taint[0..7]` initialized to `False` — note that **whenever ANY slot `i` becomes tainted (via `{op_tload}` or `{op_mix}`), `sticky_taint[i]` becomes `True` permanently for that slot index**, AND on `{op_rtx}` (rollback) values/copyable/uninit/partners revert to the transaction snapshot **EXCEPT any `sticky_taint[i]` set during the aborted transaction persists into `slots[i].tainted` (if slot `i` exists in the restored snapshot)**!
        Process instructions `(op, a, b)` sequentially (`a`, `b` may be `None`):
        - `{op_load}` `(slot, imm)`: clears any partner on `slot`, sets `slots[slot] = (imm % {p_dsl}, copyable=True, tainted=False, partner=None)`.
        - `{op_tload}` `(slot, imm)`: clears any partner on `slot`, sets `slots[slot] = (imm % {p_dsl}, copyable=False, tainted=True, partner=None)` and marks `sticky_taint[slot] = True`.
        - `{op_move}` `(dst, src)`: requires `src` initialized and `dst != src` (else return `"ERR_AFFINE"`). Clears any partner on `dst`, moves `src` to `dst` (updating partner's back-pointer to `dst`), sets `src = UNINIT`. If moved record is tainted, mark `sticky_taint[dst] = True`.
        - `{op_clone}` `(dst, src)`: requires `src` initialized, `dst != src`, and `src.copyable == True` (else `"ERR_AFFINE"`). Clears any partner on `dst`, copies `(val, copyable, tainted, partner=None)` to `dst` (`src` keeps partner). If tainted, mark `sticky_taint[dst] = True`.
        - `{op_ent}` `(x, y)`: requires `x != y`, both initialized, neither partnered (else `"ERR_STATE"`). Sets `x.partner = y`, `y.partner = x`.
        - `{op_split}` `(x, y)`: requires `x` and `y` partnered with each other (else `"ERR_STATE"`). Clears both partners.
        - `{op_mix}` `(dst, src)`: requires both initialized and `dst != src` (else `"ERR_AFFINE"`). Sets `dst.val = (dst.val + src.val) % {p_dsl}` and `dst.tainted |= src.tainted`. If `dst.tainted`, mark `sticky_taint[dst] = True`. If `dst` has partner `p`: ALSO set `p.val = (p.val - src.val) % {p_dsl}` and `p.tainted |= dst.tainted` (and if `p.tainted`, mark `sticky_taint[p] = True`).
        - `{op_btx}` `(None, None)`: pushes a transaction snapshot of `slots` onto a TX stack (supports nesting).
        - `{op_ctx}` `(None, None)`: commits (pops top snapshot; if stack empty return `"ERR_STATE"`).
        - `{op_rtx}` `(None, None)`: pops top snapshot (if empty return `"ERR_STATE"`), restores `slots` to that snapshot, AND for every index `i` where `slots[i]` is initialized and `sticky_taint[i]` is `True`, sets `slots[i].tainted = True`!
        - `{op_seal}` `(slot, None)`: requires `slot` initialized (`"ERR_AFFINE"`). If `slot.tainted` OR (`slot.partner != None` and `slots[slot.partner].tainted`), return `"ERR_TAINT"`.
        At the end (if stack still non-empty, return `"ERR_STATE"`), return `[s[0] if s is not None else None for s in slots]`. Output ONLY Python code in a ```python block.""")

    ref_dsl_code = textwrap.dedent(f"""\
        def analyze_tx_machine(program):
            slots = [None] * 8
            sticky = [False] * 8
            tx = []
            def unlink(i):
                if slots[i] is not None and slots[i][3] is not None:
                    p = slots[i][3]
                    if slots[p] is not None:
                        slots[p][3] = None
                    slots[i][3] = None
            for op, a, b in program:
                if op == {op_load!r}:
                    unlink(a)
                    slots[a] = [b % {p_dsl}, True, False, None]
                elif op == {op_tload!r}:
                    unlink(a)
                    slots[a] = [b % {p_dsl}, False, True, None]
                    sticky[a] = True
                elif op == {op_move!r}:
                    if a == b or slots[b] is None:
                        return "ERR_AFFINE"
                    unlink(a)
                    rec = slots[b]; slots[b] = None; slots[a] = rec
                    if rec[3] is not None and slots[rec[3]] is not None:
                        slots[rec[3]][3] = a
                    if rec[2]: sticky[a] = True
                elif op == {op_clone!r}:
                    if a == b or slots[b] is None or not slots[b][1]:
                        return "ERR_AFFINE"
                    unlink(a)
                    rec = slots[b]
                    slots[a] = [rec[0], rec[1], rec[2], None]
                    if rec[2]: sticky[a] = True
                elif op == {op_ent!r}:
                    if a == b or slots[a] is None or slots[b] is None or slots[a][3] is not None or slots[b][3] is not None:
                        return "ERR_STATE"
                    slots[a][3] = b; slots[b][3] = a
                elif op == {op_split!r}:
                    if slots[a] is None or slots[b] is None or slots[a][3] != b or slots[b][3] != a:
                        return "ERR_STATE"
                    slots[a][3] = None; slots[b][3] = None
                elif op == {op_mix!r}:
                    if a == b or slots[a] is None or slots[b] is None:
                        return "ERR_AFFINE"
                    slots[a][0] = (slots[a][0] + slots[b][0]) % {p_dsl}
                    slots[a][2] = slots[a][2] or slots[b][2]
                    if slots[a][2]: sticky[a] = True
                    p = slots[a][3]
                    if p is not None and slots[p] is not None:
                        slots[p][0] = (slots[p][0] - slots[b][0]) % {p_dsl}
                        slots[p][2] = slots[p][2] or slots[a][2]
                        if slots[p][2]: sticky[p] = True
                elif op == {op_btx!r}:
                    tx.append([list(s) if s is not None else None for s in slots])
                elif op == {op_ctx!r}:
                    if not tx: return "ERR_STATE"
                    tx.pop()
                elif op == {op_rtx!r}:
                    if not tx: return "ERR_STATE"
                    slots = tx.pop()
                    for i in range(8):
                        if slots[i] is not None and sticky[i]:
                            slots[i][2] = True
                elif op == {op_seal!r}:
                    if slots[a] is None: return "ERR_AFFINE"
                    p = slots[a][3]
                    if slots[a][2] or (p is not None and slots[p] is not None and slots[p][2]):
                        return "ERR_TAINT"
            if tx:
                return "ERR_STATE"
            return [s[0] if s is not None else None for s in slots]
    """)
    ns_dsl = {}
    exec(ref_dsl_code, ns_dsl)
    ref_dsl_fn = ns_dsl["analyze_tx_machine"]
    dsl_programs = [
        # 1. Entangled + move + TX rollback restores values/partner, then seal passes when clean
        [(op_load, 0, 15), (op_load, 1, 22), (op_load, 2, 7), (op_ent, 0, 1), (op_btx, None, None), (op_mix, 0, 2), (op_move, 4, 0), (op_rtx, None, None), (op_mix, 0, 2), (op_seal, 1, None), (op_split, 0, 1)],
        # 2. Monotonic taint side-channel survives TX_ROLLBACK -> ERR_TAINT on partner!
        [(op_load, 0, 10), (op_load, 1, 20), (op_ent, 0, 1), (op_btx, None, None), (op_tload, 3, 5), (op_mix, 0, 3), (op_rtx, None, None), (op_seal, 0, None)],
        # 3. Partner sticky taint survives rollback -> ERR_TAINT on slot 1 via rolled-back partner 0!
        [(op_load, 0, 10), (op_load, 1, 20), (op_ent, 0, 1), (op_btx, None, None), (op_tload, 3, 5), (op_mix, 0, 3), (op_rtx, None, None), (op_seal, 1, None)],
        # 4. Nested TX commit + unclosed TX err
        [(op_load, 0, 12), (op_btx, None, None), (op_load, 2, 5), (op_btx, None, None), (op_mix, 0, 2), (op_ctx, None, None), (op_ctx, None, None)],
        # 5. Unclosed TX -> ERR_STATE
        [(op_load, 0, 12), (op_btx, None, None), (op_load, 1, 5)],
    ]
    dsl_cases = [(p, ref_dsl_fn(p)) for p in dsl_programs]
    dsl_harness = f"""
cases = {repr(dsl_cases)}
for prog, exp in cases:
    got = analyze_tx_machine(list(prog))
    assert got == exp, f"expected {{exp}}, got {{got}}"
"""
    frontier.append({
        "id": "front_ood_affine_tx_taint",
        "category": "frontier_ceiling",
        "prompt": dsl_prompt,
        "ref_code": ref_dsl_code,
        "verify": lambda resp, h=dsl_harness: run_python_tests(extract_code(resp), h),
    })

    return frontier


def build_all_tasks(seed: int, suite: str = "v1") -> list[dict]:
    tasks = build_tasks(seed)
    if suite in ("v2", "v3"):
        tasks.extend(build_v2_extra_tasks(seed))
    if suite == "v3":
        tasks.extend(build_v3_frontier_tasks(seed))
    return tasks


def self_test(suite: str = "v1"):
    tasks = build_all_tasks(SEED, suite)
    exp_n = 28 if suite == "v3" else (24 if suite == "v2" else 16)
    assert len(tasks) == exp_n, f"expected {exp_n}, got {len(tasks)}"
    for t in tasks:
        if "verify_msg" in t:
            ok, msg = t["verify_msg"](t["ref_msg"])
        elif "ref_code" in t:
            ok, msg = t["verify"](f"```python\n{t['ref_code']}\n```")
        elif t["id"] == "struct_negative_constraints":
            sample = (
                "1. Signature check validates incoming frames before queue entry.\n"
                "2. Oversized payloads split into fixed pieces for compression.\n"
                "3. Shards store pieces redundantly with verified ledger checksums.\n"
                "4. Replica quorum confirms write commits before caller return."
            )
            ok, msg = t["verify"](sample)
        elif t["id"].startswith("struct_") or t["id"] in ("ctx_4k_diff_audit", "ctx_16k_state_mutation", "front_16k_rollback_audit"):
            ok, msg = t["verify"](t["ref_answer"])
        else:
            ok, msg = t["verify"](f"FINAL: {t['ref_answer']}")
        assert ok, f"SELF-TEST FAILED on {t['id']}: {msg}"
    print(f"SELF-TEST PASSED ({suite}): {len(tasks)}/{exp_n} salted tasks verified.")




def call_model(url: str, model: str, messages: list[dict] | str, sampler: dict, max_tokens: int = 8192, extra_kwargs: dict | None = None, tools: list[dict] | None = None, timeout_s: int = 1200):
    msgs = [{"role": "user", "content": messages}] if isinstance(messages, str) else messages
    body = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "cache_prompt": False,
        **sampler,
    }
    if extra_kwargs:
        body["chat_template_kwargs"] = extra_kwargs
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        wall = time.monotonic() - t0
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        fin = "env_limit" if e.code == 400 and ("context" in err_body.lower() or "exceed" in err_body.lower() or "memory" in err_body.lower()) else f"http_{e.code}"
        return {
            "content": "", "tool_calls": [], "reasoning_len": 0, "content_len": 0,
            "finish_reason": fin, "error_detail": f"HTTP {e.code}: {err_body[:220]}",
            "wall_s": round(wall, 3), "prompt_n": 0, "predicted_n": 0,
            "pp_tps": 0.0, "tg_tps": 0.0, "draft_n": 0, "draft_n_accepted": 0, "completion_tokens": 0,
        }
    except Exception as e:
        wall = time.monotonic() - t0
        fin = "timeout" if "timed out" in str(e).lower() else "error"
        return {
            "content": "", "tool_calls": [], "reasoning_len": 0, "content_len": 0,
            "finish_reason": fin, "error_detail": str(e)[:220],
            "wall_s": round(wall, 3), "prompt_n": 0, "predicted_n": 0,
            "pp_tps": 0.0, "tg_tps": 0.0, "draft_n": 0, "draft_n_accepted": 0, "completion_tokens": 0,
        }
    wall = time.monotonic() - t0
    ch = data["choices"][0]
    msg = ch["message"]
    usage = data.get("usage") or {}
    timings = data.get("timings") or {}
    raw_content = msg.get("content") or ""
    reasoning_len = len(msg.get("reasoning_content") or "")
    if not reasoning_len:
        m_think = re.match(r"^\s*(?:<think>|<\|thought\|>)(.*?)(?:</think>|<\|/thought\|>)\s*(.*)$", raw_content, flags=re.S)
        if m_think:
            reasoning_len = len(m_think.group(1))
            raw_content = m_think.group(2)
    return {
        "content": raw_content,
        "tool_calls": msg.get("tool_calls") or [],
        "reasoning_len": reasoning_len,
        "content_len": len(raw_content),
        "finish_reason": ch.get("finish_reason"),
        "wall_s": round(wall, 3),
        "prompt_n": timings.get("prompt_n", usage.get("prompt_tokens", 0)),
        "predicted_n": timings.get("predicted_n", usage.get("completion_tokens", 0)),
        "pp_tps": round(timings.get("prompt_per_second", 0.0), 2),
        "tg_tps": round(timings.get("predicted_per_second", 0.0), 2),
        "draft_n": timings.get("draft_n", 0),
        "draft_n_accepted": timings.get("draft_n_accepted", 0),
        "completion_tokens": usage.get("completion_tokens"),
    }


def read_mem_snapshot():
    import glob
    # Check AMD DRM sysfs (Strix Halo UMA) first, fallback nvidia-smi, else /proc/meminfo
    gtt = 0
    vram = 0
    for p in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_gtt_used")):
        try:
            gtt = int(Path(p).read_text().strip()) // (1024 * 1024)
            break
        except Exception:
            pass
    for p in sorted(glob.glob("/sys/class/drm/card*/device/mem_info_vram_used")):
        try:
            vram = int(Path(p).read_text().strip()) // (1024 * 1024)
            break
        except Exception:
            pass
    nv_vram = 0
    if gtt == 0:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                text=True,
                timeout=3,
            ).strip()
            nv_vram = max(int(x.strip()) for x in out.splitlines() if x.strip())
        except Exception:
            pass
    avail_mib = 0
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                avail_mib = int(ln.split()[1]) // 1024
                break
    except Exception:
        pass
    return {"gtt_mib": gtt, "vram_mib": nv_vram or vram, "mem_avail_mib": avail_mib}


def summarize_run(meta: dict, rows: list[dict]) -> dict:
    n = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    trunc = sum(1 for r in rows if r["finish_reason"] == "length")
    walls = [r["wall_s"] for r in rows]
    gns = [r["predicted_n"] for r in rows]
    pps = [r["pp_tps"] for r in rows]
    tgs = [r["tg_tps"] for r in rows]
    d_acc = sum(r.get("draft_n_accepted", 0) for r in rows)
    d_tot = sum(r.get("draft_n", 0) for r in rows)
    p90_idx = max(0, min(n - 1, int(round(0.9 * (n - 1)))))
    sorted_walls = sorted(walls)
    cats = {}
    for cat_key in ("coding", "reasoning", "structured", "long_context", "agent_tools", "ops_and_voice", "frontier_ceiling"):
        crows = [r for r in rows if r["category"] == cat_key]
        if crows:
            cats[cat_key] = {
                "pass_count": sum(1 for r in crows if r["passed"]),
                "total": len(crows),
                "pass_pct": round(100.0 * sum(1 for r in crows if r["passed"]) / len(crows), 1),
                "wall_median_s": round(statistics.median(r["wall_s"] for r in crows), 2),
                "wall_total_s": round(sum(r["wall_s"] for r in crows), 2),
                "gen_tok_median": int(round(statistics.median(r["predicted_n"] for r in crows))),
            }
    return {
        "schema": "salted-eval/v1",
        **meta,
        "summary": {
            "pass_count": passed,
            "total_tasks": n,
            "pass_pct": round(100.0 * passed / n, 1) if n else 0.0,
            "truncated": trunc,
            "wall_total_s": round(sum(walls), 2),
            "wall_total_min": round(sum(walls) / 60.0, 2),
            "wall_median_s": round(statistics.median(walls), 2),
            "wall_mean_s": round(statistics.mean(walls), 2),
            "wall_p90_s": round(sorted_walls[p90_idx], 2),
            "gen_tok_total": sum(gns),
            "gen_tok_median": int(round(statistics.median(gns))),
            "pp_tps_median": round(statistics.median(pps), 1),
            "pp_tps_max": round(max(pps), 1),
            "tg_tps_median": round(statistics.median(tgs), 1),
            "draft_accepted": d_acc,
            "draft_total": d_tot,
            "draft_accept_pct": round(100.0 * d_acc / d_tot, 1) if d_tot else None,
        },
        "categories": cats,
        "tasks": rows,
    }


def main():
    from pathlib import Path
    import datetime as dt
    ap = argparse.ArgumentParser(description="Salted 16-task benchmark emitting salted-eval/v1 JSON bundles.")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--seed", type=int, default=20261124)
    ap.add_argument("--url", default="http://127.0.0.1:8099/v1/chat/completions")
    ap.add_argument("--run-id", default="custom-run")
    ap.add_argument("--phase", default="Custom")
    ap.add_argument("--host-label", default="lab")
    ap.add_argument("--hardware", default="unknown")
    ap.add_argument("--engine", default="llama.cpp")
    ap.add_argument("--backend", default="unknown")
    ap.add_argument("--model", default="model")
    ap.add_argument("--quant", default="unknown")
    ap.add_argument("--draft-model", default=None)
    ap.add_argument("--effort", default="default")
    ap.add_argument("--suite", choices=["v1", "v2", "v3"], default="v1")
    ap.add_argument("--subset", choices=["all", "coding", "coding_reasoning", "frontier"], default="all")
    ap.add_argument("--ctx", type=int, default=131072)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--top-k", type=int, default=0)
    ap.add_argument("--min-p", type=float, default=0.0)
    ap.add_argument("--presence-penalty", type=float, default=0.0)
    ap.add_argument("--repeat-penalty", type=float, default=1.0)
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--enable-thinking", choices=["true", "false"], default=None)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--power-target", default="auto", help="auto | node1 | node2 | ssh:node2 | none")
    ap.add_argument("--min-pp-tps", type=float, default=None)
    ap.add_argument("--min-tg-tps", type=float, default=None)
    ap.add_argument("--min-gpu-util", type=float, default=None)
    ap.add_argument("--abort-on-bottleneck", action="store_true")
    ap.add_argument("--keep-tainted-on-resume", action="store_true", help="Do not automatically drop prior tasks that failed perf_floor when resuming")
    ap.add_argument("--invalidate-reason", default=None, help="Explicitly mark run_validity.status=invalidated with reason")
    ap.add_argument("--retry-truncated-from", default=None, help="Reuse non-length tasks from a prior JSON and re-run only length-truncated tasks")
    ap.add_argument("--resume-from", default=None, help="Resume from a .partial.json checkpoint matching the same task list")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", required=False, default="/tmp/salted_run.json")
    args = ap.parse_args()

    global SEED
    SEED = args.seed
    if args.self_test:
        self_test("v1")
        self_test("v2")
        self_test("v3")
        return

    tasks = build_all_tasks(args.seed, args.suite)
    if args.subset == "coding":
        tasks = [t for t in tasks if t["category"] == "coding"]
    elif args.subset == "coding_reasoning":
        tasks = [t for t in tasks if t["category"] in ("coding", "reasoning")]
    elif args.subset == "frontier":
        tasks = [t for t in tasks if t["category"] == "frontier_ceiling"]

    sampler = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "min_p": args.min_p,
        "presence_penalty": args.presence_penalty,
        "repeat_penalty": args.repeat_penalty,
        "seed": args.seed,
    }
    extra = {}
    if args.reasoning_effort:
        extra["reasoning_effort"] = args.reasoning_effort
    if args.enable_thinking is not None:
        extra["enable_thinking"] = (args.enable_thinking == "true")
    extra = extra or None

    prior_by_id = {}
    extended_from = None
    if args.retry_truncated_from:
        base_doc = json.loads(Path(args.retry_truncated_from).read_text())
        extended_from = base_doc.get("run_id") or Path(args.retry_truncated_from).stem
        for r in base_doc.get("tasks", []):
            if r.get("finish_reason") != "length":
                prior_by_id[r["id"]] = r
        print(f"[{args.run_id}] Extending {extended_from}: keeping {len(prior_by_id)}/{len(tasks)} non-truncated tasks; re-running {len(tasks)-len(prior_by_id)} truncated tasks @ max_tokens={args.max_tokens}", flush=True)
    elif args.resume_from and Path(args.resume_from).exists():
        pdoc = json.loads(Path(args.resume_from).read_text())
        for r in pdoc.get("tasks", []):
            prior_by_id[r["id"]] = r
        print(f"[{args.run_id}] Resuming from {args.resume_from}: {len(prior_by_id)}/{len(tasks)} completed", flush=True)

    llama_ver = detect_llama_version(args.url) if detect_llama_version is not None else None
    if llama_ver:
        print(f"[{args.run_id}] Server build: {llama_ver.get('props_build_info') or llama_ver.get('label') or 'unknown'} | n_ctx={llama_ver.get('n_ctx')}", flush=True)

    # Resolve in-flight perf floors from model-profiles.json + CLI overrides
    floors = {}
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
    if args.min_pp_tps is not None:
        floors["min_pp_tps"] = args.min_pp_tps
    if args.min_tg_tps is not None:
        floors["min_tg_tps"] = args.min_tg_tps
    if args.min_gpu_util is not None:
        floors["min_gpu_util_pct"] = args.min_gpu_util

    # Drop any resumed prior tasks that ran under a hardware/config bottleneck so tainted scores aren't baked in
    if prior_by_id and floors and check_perf_floor is not None and not args.keep_tainted_on_resume and not args.retry_truncated_from:
        dropped = []
        for tid, row in list(prior_by_id.items()):
            if row.get("error_detail"):
                continue
            ok_p, warn_p = check_perf_floor(
                pp_tps=row.get("pp_tps"),
                tg_tps=row.get("tg_tps"),
                floors={k: v for k, v in floors.items() if k != "min_gpu_util_pct"},
                prompt_n=row.get("prompt_n"),
                predicted_n=row.get("predicted_n"),
            )
            if not ok_p:
                dropped.append(f"{tid} ({'; '.join(warn_p)})")
                prior_by_id.pop(tid, None)
        if dropped:
            print(f"[{args.run_id}] [RESUME_INVALIDATED] Dropped {len(dropped)} prior task(s) violating perf_floor: {', '.join(dropped[:5])}", flush=True)

    print(f"[{args.run_id}] Warming up {args.model} ({len(tasks)} tasks, seed={args.seed})...", flush=True)
    warm_res = call_model(
        args.url,
        args.model,
        "[WARMUP] Briefly explain in 5 sentences why database B-trees differ from LSM-trees on write amplification.",
        sampler,
        max_tokens=300,
        extra_kwargs=extra,
        timeout_s=args.timeout,
    )
    perf_warnings = []
    if check_perf_floor is not None and floors and not warm_res.get("error_detail"):
        snap = None
        if sample_once is not None and args.power_target != "none":
            try:
                snap = sample_once(args.power_target)
            except Exception:
                pass
        w_floors = {k: v for k, v in floors.items() if k == "min_gpu_util_pct"}  # 16-tok warmup is too short for pp/tg floors
        ok_w, warn_w = check_perf_floor(
            gpu_util_pct=snap.get("gpu_util_pct") if snap else None,
            floors=w_floors,
        )
        print(f"[{args.run_id}] Warmup perf: tg={warm_res.get('tg_tps')} t/s gpu_util={snap.get('gpu_util_pct') if snap else '?'}% -> {'OK' if ok_w else 'WARN: ' + '; '.join(warn_w)}", flush=True)
        if not ok_w:
            perf_warnings.extend([f"warmup: {w}" for w in warn_w])

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    partial_p = out_p.with_suffix(".partial.json")

    ps_ctx = None
    if PowerSampler is not None and args.power_target != "none":
        try:
            ps_ctx = PowerSampler(args.power_target, interval_s=2.0)
            ps_ctx.__enter__()
        except Exception:
            ps_ctx = None

    rows = []
    peak_gtt = 0
    peak_vram = 0
    try:
        for idx, t in enumerate(tasks, 1):
            if t["id"] in prior_by_id:
                row = prior_by_id[t["id"]]
                rows.append(row)
                peak_gtt = max(peak_gtt, row.get("gtt_mib", 0) or 0)
                peak_vram = max(peak_vram, row.get("vram_mib", 0) or 0)
                print(f"  [{idx:02d}/{len(tasks):02d}] {t['id']:28s} {'PASS' if row['passed'] else 'FAIL'} | (reused from prior run)", flush=True)
                continue
            msgs = t.get("messages", t.get("prompt"))
            res = call_model(args.url, args.model, msgs, sampler, max_tokens=args.max_tokens, extra_kwargs=extra, tools=t.get("tools"), timeout_s=args.timeout)
            if res.get("error_detail"):
                ok = False
                detail = res["error_detail"]
            else:
                if "verify_msg" in t:
                    ok, detail = t["verify_msg"](res)
                else:
                    ok, detail = t["verify"](res["content"])
                if res["finish_reason"] == "length":
                    ok = False
                    detail = f"truncated (length): {detail}"
            mem = read_mem_snapshot()
            peak_gtt = max(peak_gtt, mem["gtt_mib"])
            peak_vram = max(peak_vram, mem["vram_mib"])
            row = {
                "id": t["id"],
                "category": t["category"],
                "passed": ok,
                "detail": detail,
                **mem,
                **{k: v for k, v in res.items() if k not in ("content", "tool_calls")},
            }
            rows.append(row)
            partial_p.write_text(json.dumps({"run_id": args.run_id, "tasks": rows}, indent=2))
            print(
                f"  [{idx:02d}/{len(tasks):02d}] {t['id']:28s} {'PASS' if ok else 'FAIL'} | "
                f"wall={row['wall_s']:6.2f}s pp={row['pp_tps']:6.1f} tg={row['tg_tps']:5.1f} "
                f"gn={row['predicted_n']} fin={row['finish_reason']}",
                flush=True,
            )
            if check_perf_floor is not None and floors and not res.get("error_detail") and row["predicted_n"] >= 64:
                ok_t, warn_t = check_perf_floor(
                    pp_tps=row.get("pp_tps"),
                    tg_tps=row.get("tg_tps"),
                    floors=floors,
                    prompt_n=row.get("prompt_n"),
                    predicted_n=row.get("predicted_n"),
                )
                if not ok_t:
                    msg = f"{t['id']}: {'; '.join(warn_t)}"
                    perf_warnings.append(msg)
                    print(f"    [BOTTLENECK_WARN] {msg}", flush=True)
                    if args.abort_on_bottleneck and len(perf_warnings) >= 2:
                        raise RuntimeError(f"Aborted on in-flight performance bottleneck: {perf_warnings}")
    finally:
        if ps_ctx is not None:
            ps_ctx.__exit__(None, None, None)

    mem_label = f"{peak_gtt} MiB GTT" if peak_gtt else f"{peak_vram} MiB VRAM"
    pwr_summary = None
    if ps_ctx is not None:
        pwr_summary = ps_ctx.summary(
            total_gen_tokens=sum(r.get("predicted_n", 0) or 0 for r in rows),
            pass_count=sum(1 for r in rows if r.get("passed")),
        )
    env_lim_cnt = sum(1 for r in rows if r.get("finish_reason") == "env_limit")
    # Evaluate run-level medians against perf_floor so isolated draft-acceptance variance on prose doesn't false-invalidate a healthy run
    med_tg = statistics.median([r["tg_tps"] for r in rows if r.get("tg_tps") and r.get("predicted_n", 0) >= 64]) if any(r.get("tg_tps") and r.get("predicted_n", 0) >= 64 for r in rows) else None
    long_pps = [r["pp_tps"] for r in rows if r.get("pp_tps") and (r.get("prompt_n") or 0) >= 1000]
    med_pp_long = statistics.median(long_pps) if long_pps else None
    med_ok, med_warn = (True, [])
    if check_perf_floor is not None and floors:
        med_ok, med_warn = check_perf_floor(
            pp_tps=med_pp_long, tg_tps=med_tg,
            gpu_util_pct=(pwr_summary or {}).get("gpu_util_avg_pct"),
            floors=floors, prompt_n=1000 if med_pp_long is not None else 999999, predicted_n=64,
        )
    if args.invalidate_reason:
        val_status, val_reason = "invalidated", args.invalidate_reason
    elif not med_ok or len(perf_warnings) >= max(4, len(rows) // 2):
        why = "; ".join(med_warn) if not med_ok else perf_warnings[0]
        val_status, val_reason = "invalidated", f"Perf bottleneck ({len(perf_warnings)} warnings): {why}"
    elif env_lim_cnt > 0:
        val_status, val_reason = "env_limit_impacted", f"{env_lim_cnt} task(s) hit context/memory env_limit"
    else:
        val_status, val_reason = "valid", None
    meta = {
        "run_id": args.run_id,
        "phase": args.phase,
        "suite": args.suite,
        "seed": args.seed,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host_label": args.host_label,
        "hardware": args.hardware,
        "engine": args.engine,
        "backend": args.backend,
        "model": args.model,
        "quant": args.quant,
        "draft_model": args.draft_model,
        "effort": args.effort,
        "subset": args.subset,
        "ctx": args.ctx,
        "max_tokens": args.max_tokens,
        "extended_from": extended_from,
        "llama_version": llama_ver,
        "perf_validation": {"floors": floors or None, "warnings": perf_warnings, "passed": len(perf_warnings) == 0},
        "run_validity": {
            "status": val_status,
            "reason": val_reason,
            "perf_floor_violations": len(perf_warnings),
            "env_limit_tasks": env_lim_cnt,
            "llama_build": (llama_ver or {}).get("label") or (llama_ver or {}).get("props_build_info"),
        },
        "power_telemetry": pwr_summary,
        "sampler": sampler,
        "mem_label": mem_label,
        "mem_peak_mib": peak_gtt or peak_vram,
        "notes": args.notes,
    }
    doc = summarize_run(meta, rows)
    out_p.write_text(json.dumps(doc, indent=2))
    if partial_p.exists():
        partial_p.unlink()
    print(f"[{args.run_id}] Saved {out_p} ({doc['summary']['pass_count']}/{doc['summary']['total_tasks']})", flush=True)


if __name__ == "__main__":
    main()
