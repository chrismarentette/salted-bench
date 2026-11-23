#!/usr/bin/env python3
"""Zero-sudo hardware power & clock sampler for node1 (RTX 4060 + i5-14400F) and node2 (Strix Halo APU).

Measures:
  - node2 (AMD Strix Halo): direct socket PPT watts via /sys/class/hwmon/hwmon*/power1_average
    (fallback power1_input) + GPU SCLK (freq1_input) + package/edge temps.
  - node1 (RTX 4060 + i5-14400F): NVIDIA `nvidia-smi -q -d POWER,CLOCK,TEMPERATURE` Power Samples
    (Avg/Min/Max W, since scalar power.draw reports [N/A] on this board) + GPU graphics/mem clocks +
    /proc/stat CPU utilization mapped onto i5-14400F 12W..65W TDP curve + ~18W RAM/board baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _find_amdgpu_hwmon_local() -> Path | None:
    for h in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (h / "name").read_text().strip() == "amdgpu":
                return h
        except OSError:
            pass
    return None


def sample_node1_local(cpu_window_s: float = 0.25) -> dict[str, Any]:
    out = subprocess.check_output(
        ["nvidia-smi", "-q", "-d", "POWER,CLOCK,UTILIZATION,TEMPERATURE"],
        text=True,
        timeout=5,
    )
    avg_m = re.search(r"Power Samples.*?Avg\s*:\s*([\d.]+)\s*W", out, re.S)
    min_m = re.search(r"Power Samples.*?Min\s*:\s*([\d.]+)\s*W", out, re.S)
    max_m = re.search(r"Power Samples.*?Max\s*:\s*([\d.]+)\s*W", out, re.S)
    scalar_m = re.search(r"Average Power Draw\s*:\s*([\d.]+)\s*W", out)
    gclk_m = re.search(r"Clocks\s*\n\s*Graphics\s*:\s*(\d+)\s*MHz", out)
    mclk_m = re.search(r"Clocks.*?Memory\s*:\s*(\d+)\s*MHz", out, re.S)
    util_m = re.search(r"GPU Utilization Samples.*?Avg\s*:\s*(\d+)\s*%", out, re.S) or re.search(r"Utilization\s*\n\s*Gpu\s*:\s*(\d+)\s*%", out)
    temp_m = re.search(r"GPU Current Temp\s*:\s*(\d+)\s*C", out)

    def _read_cpu():
        vals = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)

    t1, i1 = _read_cpu()
    time.sleep(max(0.05, cpu_window_s))
    t2, i2 = _read_cpu()
    cpu_pct = 100.0 * (1.0 - (i2 - i1) / max(1, t2 - t1))

    # CPU freq average (kHz -> MHz)
    freqs = []
    for pf in sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_cur_freq")):
        try:
            freqs.append(int(pf.read_text().strip()) / 1000.0)
        except OSError:
            pass
    cpu_mhz = round(sum(freqs) / len(freqs), 1) if freqs else None

    gpu_w = float(avg_m.group(1)) if avg_m else (float(scalar_m.group(1)) if scalar_m else None)
    gpu_min_w = float(min_m.group(1)) if min_m else gpu_w
    gpu_max_w = float(max_m.group(1)) if max_m else gpu_w
    # i5-14400F: ~12W idle pkg to 65W PL1 sustained TDP; +18W DDR5/board/fans baseline
    cpu_est_w = round(12.0 + (cpu_pct / 100.0) * 53.0, 2)
    board_est_w = 18.0
    sys_est_w = round((gpu_w or 0.0) + cpu_est_w + board_est_w, 2) if gpu_w is not None else None

    return {
        "host": "node1",
        "source": "nvidia_power_samples+cpu_tdp_est",
        "gpu_or_apu_w": round(gpu_w, 2) if gpu_w is not None else None,
        "gpu_min_w": round(gpu_min_w, 2) if gpu_min_w is not None else None,
        "gpu_max_w": round(gpu_max_w, 2) if gpu_max_w is not None else None,
        "cpu_est_w": cpu_est_w,
        "system_est_w": sys_est_w,
        "gpu_clock_mhz": int(gclk_m.group(1)) if gclk_m else None,
        "mem_clock_mhz": int(mclk_m.group(1)) if mclk_m else None,
        "cpu_clock_mhz": cpu_mhz,
        "cpu_busy_pct": round(cpu_pct, 1),
        "gpu_util_pct": int(util_m.group(1)) if util_m else None,
        "temp_c": int(temp_m.group(1)) if temp_m else None,
        "ts": time.time(),
    }


def sample_node2_local() -> dict[str, Any]:
    hw = _find_amdgpu_hwmon_local()
    if hw is None:
        raise RuntimeError("amdgpu hwmon sensor not found")
    p_avg = hw / "power1_average"
    p_inp = hw / "power1_input"
    uw = int((p_avg if p_avg.exists() else p_inp).read_text().strip())
    apu_w = round(uw / 1_000_000.0, 2)
    sclk = int((hw / "freq1_input").read_text().strip()) // 1_000_000 if (hw / "freq1_input").exists() else None
    edge_c = round(int((hw / "temp1_input").read_text().strip()) / 1000.0, 1) if (hw / "temp1_input").exists() else None
    tctl_c = None
    for kh in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (kh / "name").read_text().strip() == "k10temp" and (kh / "temp1_input").exists():
                tctl_c = round(int((kh / "temp1_input").read_text().strip()) / 1000.0, 1)
                break
        except OSError:
            pass
    # Strix Halo APU PPT captures CPU+GPU+SoC socket (~14W DRAM/SSD/fan board overhead)
    sys_est_w = round(apu_w + 14.0, 2)
    return {
        "host": "node2",
        "source": "amdgpu_hwmon_ppt",
        "gpu_or_apu_w": apu_w,
        "gpu_min_w": apu_w,
        "gpu_max_w": apu_w,
        "cpu_est_w": None,
        "system_est_w": sys_est_w,
        "gpu_clock_mhz": sclk,
        "mem_clock_mhz": None,
        "cpu_clock_mhz": None,
        "cpu_busy_pct": None,
        "temp_c": max([x for x in (edge_c, tctl_c) if x is not None], default=None),
        "ts": time.time(),
    }


def sample_remote_node2(ssh_host: str = "node2") -> dict[str, Any]:
    cmd = (
        "python3 -c 'import glob, pathlib, time; "
        "hw=[p for p in sorted(pathlib.Path(\"/sys/class/hwmon\").glob(\"hwmon*\")) "
        "if (p/\"name\").exists() and (p/\"name\").read_text().strip()==\"amdgpu\"][0]; "
        "pf=hw/\"power1_average\" if (hw/\"power1_average\").exists() else hw/\"power1_input\"; "
        "uw=int(pf.read_text().strip()); "
        "sc=int((hw/\"freq1_input\").read_text().strip())//1000000 if (hw/\"freq1_input\").exists() else None; "
        "ec=round(int((hw/\"temp1_input\").read_text().strip())/1000,1) if (hw/\"temp1_input\").exists() else None; "
        "import json; print(json.dumps({\"host\":\"node2\",\"source\":\"amdgpu_hwmon_ppt\","
        "\"gpu_or_apu_w\":round(uw/1e6,2),\"system_est_w\":round(uw/1e6+14.0,2),"
        "\"gpu_clock_mhz\":sc,\"temp_c\":ec,\"ts\":time.time()}))'"
    )
    out = subprocess.check_output(["ssh", "-o", "ConnectTimeout=5", ssh_host, cmd], text=True, timeout=10)
    return json.loads(out.strip())


def sample_once(target: str = "auto") -> dict[str, Any]:
    if target == "auto":
        if _find_amdgpu_hwmon_local() is not None:
            return sample_node2_local()
        return sample_node1_local()
    if target == "node1":
        return sample_node1_local()
    if target == "node2":
        if _find_amdgpu_hwmon_local() is not None:
            return sample_node2_local()
        return sample_remote_node2("node2")
    if target.startswith("ssh:"):
        return sample_remote_node2(target.split(":", 1)[1])
    raise ValueError(f"Unknown target: {target}")


class PowerSampler:
    """Background wattage/clock integrator across a task or full benchmark run."""

    def __init__(self, target: str = "auto", interval_s: float = 2.0, kwh_rate_usd: float = 0.15):
        self.target = target
        self.interval_s = max(0.5, interval_s)
        self.kwh_rate_usd = kwh_rate_usd
        self.samples: list[dict[str, Any]] = []
        self.errors: int = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0: float = 0.0
        self._t1: float = 0.0

    def __enter__(self) -> "PowerSampler":
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._t1 = time.monotonic()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.samples.append(sample_once(self.target))
            except Exception:
                self.errors += 1
            self._stop.wait(self.interval_s)

    def summary(self, total_gen_tokens: int = 0, pass_count: int = 0) -> dict[str, Any]:
        dur_s = max(0.001, (self._t1 or time.monotonic()) - self._t0)
        if not self.samples:
            return {
                "target": self.target,
                "samples": 0,
                "errors": self.errors,
                "wall_s": round(dur_s, 2),
                "available": False,
            }
        sys_ws = [s["system_est_w"] for s in self.samples if s.get("system_est_w") is not None]
        dev_ws = [s["gpu_or_apu_w"] for s in self.samples if s.get("gpu_or_apu_w") is not None]
        clks = [s["gpu_clock_mhz"] for s in self.samples if s.get("gpu_clock_mhz") is not None]
        temps = [s["temp_c"] for s in self.samples if s.get("temp_c") is not None]
        utils = [s["gpu_util_pct"] for s in self.samples if s.get("gpu_util_pct") is not None]
        avg_sys_w = sum(sys_ws) / len(sys_ws) if sys_ws else 0.0
        avg_dev_w = sum(dev_ws) / len(dev_ws) if dev_ws else 0.0
        peak_dev_w = max((s.get("gpu_max_w") or s.get("gpu_or_apu_w") or 0.0) for s in self.samples)
        sys_wh = avg_sys_w * (dur_s / 3600.0)
        dev_wh = avg_dev_w * (dur_s / 3600.0)
        cost_cents = (sys_wh / 1000.0) * self.kwh_rate_usd * 100.0
        return {
            "available": True,
            "host": self.samples[0].get("host"),
            "source": self.samples[0].get("source"),
            "samples": len(self.samples),
            "errors": self.errors,
            "wall_s": round(dur_s, 2),
            "device_avg_w": round(avg_dev_w, 2),
            "device_peak_w": round(peak_dev_w, 2),
            "system_avg_w": round(avg_sys_w, 2),
            "device_wh": round(dev_wh, 3),
            "system_wh": round(sys_wh, 3),
            "clock_avg_mhz": round(sum(clks) / len(clks), 1) if clks else None,
            "gpu_util_avg_pct": round(sum(utils) / len(utils), 1) if utils else None,
            "temp_max_c": max(temps) if temps else None,
            "tok_per_system_wh": round(total_gen_tokens / sys_wh, 1) if (sys_wh > 0 and total_gen_tokens > 0) else None,
            "wh_per_pass": round(sys_wh / pass_count, 3) if pass_count > 0 else None,
            "est_cost_cents": round(cost_cents, 4),
            "kwh_rate_usd": self.kwh_rate_usd,
        }


def detect_llama_version(url: str, binary_hint: str | None = None, ssh_host: str | None = None) -> dict[str, Any]:
    """Query live /props and llama-server --version so every benchmark run tracks exact llama.cpp build."""
    import urllib.parse, urllib.request
    parsed = urllib.parse.urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    info: dict[str, Any] = {"origin": origin}
    props_build: str | None = None
    props_commit: str | None = None
    if origin:
        try:
            props = json.loads(urllib.request.urlopen(f"{origin}/props", timeout=3).read().decode())
            info["n_ctx"] = props.get("default_generation_settings", {}).get("n_ctx")
            info["model_ftype"] = props.get("model_ftype")
            info["model_alias"] = props.get("model_alias")
            if props.get("build_info"):
                props_build = str(props["build_info"])
                info["props_build_info"] = props_build
                mb = re.match(r"b(\d+)-([0-9a-f]+)", props_build)
                if mb:
                    info["build"] = int(mb.group(1))
                    info["commit"] = mb.group(2)
                    info["label"] = f"b{mb.group(1)} ({mb.group(2)})"
                    props_commit = mb.group(2)
        except Exception as e:
            info["props_error"] = str(e)[:120]

    home = Path.home()
    candidates = [binary_hint] if binary_hint else [
        str(home / "llama-strix-wt-inkling/build-strix-vk-inkling/bin/llama-server"),
        str(home / "llama.cpp/build/bin/llama-server"),
        str(home / "llama-strix/build-strix-vk-b1066/bin/llama-server"),
    ]
    for bin_p in candidates:
        if not bin_p:
            continue
        try:
            cmd = [bin_p, "--version"]
            if ssh_host:
                out = subprocess.check_output(["ssh", "-o", "ConnectTimeout=4", ssh_host, f"{bin_p} --version"], stderr=subprocess.STDOUT, text=True, timeout=8)
            else:
                if not Path(bin_p).exists():
                    continue
                out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=5)
            m = re.search(r"version:\s*(.+?)\s*\(build\s*(\d+),\s*commit\s*([0-9a-f]+)\)", out)
            if m:
                if props_commit and not m.group(3).startswith(props_commit[:7]) and not props_commit.startswith(m.group(3)[:7]):
                    continue  # skip sibling binary that does not match live /props server build
                info["version"] = m.group(1)
                info["build"] = int(m.group(2))
                info["commit"] = m.group(3)
                info["binary_path"] = bin_p
                info["label"] = f"b{m.group(2)} ({m.group(3)})"
                break
            elif not props_commit:
                info["raw_version"] = out.strip()[:160]
                info["binary_path"] = bin_p
                break
        except Exception:
            continue
    return info


def check_perf_floor(
    pp_tps: float | None = None,
    tg_tps: float | None = None,
    gpu_util_pct: float | None = None,
    floors: dict[str, Any] | None = None,
    prompt_n: int | None = None,
    predicted_n: int | None = None,
) -> tuple[bool, list[str]]:
    """Validate observed throughput/utilization against expected floors to catch offloading bottlenecks early."""
    if not floors:
        return True, []
    warnings: list[str] = []
    # Only enforce min_pp_tps on sufficiently large prompts (>=1000 tok) so HTTP/tokenizer overhead isn't flagged
    if floors.get("min_pp_tps") and pp_tps is not None and (prompt_n is None or prompt_n >= 1000):
        if pp_tps < float(floors["min_pp_tps"]):
            warnings.append(f"pp_tps={pp_tps:.1f} < floor {floors['min_pp_tps']}")
    # Only enforce min_tg_tps below heavy long-context KV bandwidth cliff (<=8192 prompt tok)
    if floors.get("min_tg_tps") and tg_tps is not None and (prompt_n is None or prompt_n <= 8192) and (predicted_n is None or predicted_n >= 64):
        if tg_tps < float(floors["min_tg_tps"]):
            warnings.append(f"tg_tps={tg_tps:.1f} < floor {floors['min_tg_tps']}")
    if floors.get("min_gpu_util_pct") and gpu_util_pct is not None and gpu_util_pct < float(floors["min_gpu_util_pct"]):
        warnings.append(f"gpu_util={gpu_util_pct}% < floor {floors['min_gpu_util_pct']}%")
    return (len(warnings) == 0), warnings


def selftest() -> None:
    s1 = sample_once("node1")
    assert s1["host"] == "node1" and (s1["gpu_or_apu_w"] or 0) > 0, f"node1 probe bad: {s1}"
    with PowerSampler("node1", interval_s=0.5) as ps:
        time.sleep(1.1)
    rep = ps.summary(total_gen_tokens=500, pass_count=10)
    assert rep["available"] and rep["samples"] >= 1 and rep["system_wh"] > 0, f"sampler bad: {rep}"
    print("SELFTEST PASSED: telemetry_power.py", json.dumps(rep))


def main() -> None:
    ap = argparse.ArgumentParser(description="Hardware wattage & clock probe for node1 / node2")
    ap.add_argument("--target", default="auto", help="auto | node1 | node2 | ssh:<host>")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    print(json.dumps(sample_once(args.target), indent=2))


if __name__ == "__main__":
    main()
