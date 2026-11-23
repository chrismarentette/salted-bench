# Dual-Engine Showdown — Rig B (`128 GB Strix Halo`, `gfx1151`)

Exclusive hardware evaluation on **Rig B (`AMD Ryzen AI Max+ / Radeon 8060S gfx1151`, `121 GiB` UMA)** comparing updated **`strix-fork/llama.cpp` (Vulkan RADV)** against pure-source **`UnbiasedStrata` `maya-ik2` (`feat/amd-gfx1151-ik2`, ROCm HIP)** on **`Inkling-2-Mini-UD-Q4_K_XL` (`+ mmproj-F16`)**, with `:8085` stopped so 100% of UMA bandwidth and compute belonged to the engine under test.

- **Test Date:** 2026-11-23
- **Parity Controls:** `--seed 20261124`, `cache_prompt: false`, `--ctx 131072`, `--reasoning-effort low`, `temp=1.0`, `top_p=1.0`, `top_k=0`, `min_p=0.0`
- **Suites:** `salted-eval.py v3` (`28` tasks, `max_tokens=8192`) + `salted-agentic.py` (`15` tasks: `6` harness + `4` real-world + `1` epistemic impossibility + `4` hard frontier, `max_tokens=4096`)
- **Primary Conclusion:** On **Rig B**, **`UnbiasedStrata` (`maya-ik2` HIP + `ds4_lt` `hipBLASLt` + `mtp`)** matches accuracy (`27/28` on `v3`) and leads `11/15` vs `10/15` on `agentic`, while running **`2.22×` faster prefill** (`990.7` vs `445.5 tok/s`), **`+36.7%` faster decode** (`44.9` vs `32.9 tok/s`), **`2.03×` faster agentic completion** (`8.52` vs `17.28 min`), and **`40%–52%` lower wall-plug energy**—with full `FP16` KV cache (`23.7 GiB` UMA vs `25.8 GiB` with `q8_0` KV on Vulkan).

---

## 1. Scoreboard — Rig B (`Inkling-2-Mini-UD-Q4_K_XL`, `131k` ctx, `low`)

### A. `v3` (`28` Deterministic Parity Tasks)

| Metric | `UnbiasedStrata maya-ik2` (ROCm HIP `gfx1151` + `mtp`) | `strix-fork b13610` (Vulkan RADV + `dspark`) | `strix-fork b13603` (Prior Baseline) | Delta (`UnbiasedStrata` vs `b13610`) |
|---|---:|---:|---:|---:|
| **Pass@1** | **27/28 (96.4%)** | 27/28 (96.4%) | 27/28 (96.4%) | Parity |
| **Truncations (`length`)** | 1 (`front_nonlocal_bijection_10`) | 1 (`front_nonlocal_bijection_10`) | 1 (`front_nonlocal_bijection_10`) | Parity |
| **Run Validity** | `valid` | `valid` | `n/a` | Both verified |
| **Total Suite Wall** | **1,174.4 s (19.57 min)** | 1,835.6 s (30.59 min) | 1,894.4 s (31.57 min) | **1.56× faster (-11.0 min)** |
| **Median / Mean / P90 Wall** | **17.18 s / 41.94 s / 128.8 s** | 27.61 s / 65.56 s / 196.8 s | 34.02 s / 67.66 s / 180.8 s | **1.61× faster median** |
| **Median Prefill (`pp`)** | **990.7 tok/s** | 445.5 tok/s | 430.7 tok/s | **2.22× faster prefill** |
| **Median Decode (`tg`)** | **44.93 tok/s** | 32.88 tok/s | 32.96 tok/s | **+36.7% faster decode** |
| **Speculative Acceptance** | **63.4%** (`18,908 / 29,823`) | 54.3% (`29,781 / 54,839`) | 51.3% (`28,728 / 56,021`) | **+9.1 pp acceptance** |
| **Peak UMA Footprint (VRAM + GTT)** | **23.67 GiB** (`330` + `23,911 MiB`, `FP16` KV) | 25.77 GiB (`1,991` + `24,402 MiB`, `q8_0` KV) | — | **-2.10 GiB even at `FP16` KV** |
| **Avg Power / Suite Energy** | **125.9 W / 41.13 Wh** | 133.8 W / 68.24 Wh | — | **-39.7% energy** |

#### `v3` Category Breakdown

| Category | `UnbiasedStrata` Pass (Med Wall) | `strix-fork b13610` Pass (Med Wall) | Speedup |
|---|---:|---:|---:|
| `coding` | 4/4 (**55.77 s**) | 4/4 (70.20 s) | **1.26×** |
| `reasoning` | 4/4 (**21.68 s**) | 4/4 (40.87 s) | **1.89×** |
| `structured` | 4/4 (**11.43 s**) | 4/4 (15.52 s) | **1.36×** |
| `long_context` | 4/4 (**15.67 s**) | 4/4 (29.88 s) | **1.91×** |
| `agent_tools` | 4/4 (**2.30 s**) | 4/4 (4.25 s) | **1.85×** |
| `ops_and_voice` | 4/4 (**19.22 s**) | 4/4 (42.13 s) | **2.19×** |
| `frontier_ceiling` | 3/4 (**157.18 s**) | 3/4 (205.67 s) | **1.31×** |

`UnbiasedStrata` beat `strix-fork b13610` on **27 of 28** individual `v3` tasks (`code_bug_hunt` was the lone exception at `32.3 s` vs `25.7 s` due to longer generated tokens at `42.9` vs `30.8 tok/s`).

### B. `salted-agentic.py` (`15` Multi-Turn Sandboxed Repo Tasks)

| Metric | `UnbiasedStrata maya-ik2` (ROCm HIP + `mtp`) | `strix-fork b13610` (Vulkan RADV + `dspark`) | Delta |
|---|---:|---:|---:|
| **Pass@1 (Total)** | **11/15 (73.3%)** | 10/15 (66.7%) | **+1 pass** (`agent_oxirack_ccol_bounds`) |
| **Core + Real-World (`11t` slice)** | **11/11 (100%)** | 10/11 (90.9%) | Clean sweep on `UnbiasedStrata` |
| **Frontier Agentic (`4t` hard)** | 0/4 | 0/4 | Hit 10-turn limit on both |
| **Total Wall Time** | **511.0 s (8.52 min)** | 1,036.8 s (17.28 min) | **2.03× faster (-8.76 min)** |
| **Median Task Wall** | **27.09 s** | 60.25 s | **2.22× faster** |
| **Median Decode (`tg`)** | **44.6 tok/s** | 22.0 tok/s | **2.03× faster** |
| **Avg Power / Suite Energy** | **131.0 W / 18.61 Wh** | 134.1 W / 38.64 Wh | **-51.8% energy** |

### C. Phase 1/2 Multimodal & Router Regression Smokes (`:8099`)

- **Inkling Vision + Tools (`mmproj-F16.gguf`):** Both engines passed `17*29+5 = 498`, `get_weather({"city":"Boston"})` tool calls, and synthetic `#FF0000` PNG color recognition (`UnbiasedStrata`: `'red'` at `49.9 tok/s`, CPU `mtmd` 16t; `strix-fork b13610`: `'Red'` at `34.5 tok/s`).
- **`Qwen3.8-Flash-Next-256k` Regression Check (`strix-fork b13610`, `draft-mtp` + `mmproj-BF16` + `on-direct`):** Ready in `41.6 s` (`2,033 MiB` VRAM + `88,223 MiB` GTT), **`pp = 718.0 tok/s`**, **`tg = 55.95 tok/s`** (`59/63 = 93.7%` MTP acceptance) — zero regression vs prior `b1066`/`b11620` baseline (`~41–54 tok/s`).

---

## 2. Build, ROCm Prefix & OSS Diff Summary

### Updated `strix-fork/llama.cpp` (`build-strix-vk-inkling-v2`)
- **Version:** `1.0.0-dev (build 13610, commit c6fb1abf5)` on top of our `strix-fork` RDNA 3.5 Vulkan tree (`RADV_PERFTEST=sam`, `MESA_VK_DEVICE_SELECT=1002:*`, `$ORIGIN` RPATH).
- **Changes over prior `:8085` (`b11620`) / `b13603`:**
  - `b27e44038` / `#32704`: Inkling-2 looped-layer slot sharing after the scale pass.
  - `34ece1229` / `#32763`, `46d3b835a` / `#32799`, `ea6eff77b` / `#32833`: `reasoning_effort=none` support, `ignore_merges` vocab fix, improved Inkling parser/grammar.
  - `f085c3266` / `#32855`: `mix()` loop speedup (`v3` median wall improved `34.02 s -> 27.61 s`, coding median `100.4 s -> 70.2 s`).
  - `c43af0bb3` / `43ef33d2d` / `c6fb1abf5`: Inkling-2 DSpark block drafting, adaptive pausing / `p_min`, indexed-bias FA, Unsloth loop schema, and strided Vulkan scale.

### Pure-Source `UnbiasedStrata` HIP Port (`feat/amd-gfx1151-ik2`)
- **ROCm SDK Prefix (Ubuntu 26.04 + Kernel 7.0):** Uses the local isolated **TheRock `7.14.1`** prefix (`~/rocm/install` — provides `amdclang++`, `hipBLAS`, `hipBLASLt`, and complete `rocwmma/internal/accessors.hpp`), avoiding Ubuntu 26.04's broken apt `librocwmma-dev` and mixed `noble` ROCm packages, with zero edits to the host Vulkan RADV stack.
- **Zero Installers / Clean OSS Branch (`feat/amd-gfx1151-ik2` off `9f75192`):**
  - Ported MIT-licensed `maya-ik2` HIP kernels and host-resident/UMA dispatch (`src/ik2/{ik2_rdna.inc,ik2_off.{cu,cuh},ik2_lane.{cpp,hpp},ik2_exact.h}` + updated `ik2_main.cu`, `ik2_kernels.{cu,cuh}`, `ik2_draft.{cu,hpp}`, `ik2_cpu.{cpp,hpp}`, `ik2_iq_tables.h`) plus `data/expert-profile-ik2mini.bin`.
  - Isolated `ds4`/`hy4`/`mm3` runtime/GEMM shims (`ds4_lt.cu` `hipBLASLt` auto-tuned GEMM writer `lt-picks.txt`) under `src/ik2/compat/{ds4,hy4,mm3}/` and MIT headers under `include/strata/{hip_compat,kernels,platform}/` without colliding with upstream `src/dsv41/` or `pool.hpp`.
  - Added `--drafting` (`auto|on|off`) passthrough in `frontend/main.cpp` and clean `Makefile` target `make ik2-hip AMDGPU_TARGETS=gfx1151` (defaulting to `gfx1100;gfx1151;gfx1201`), verified with zero hardcoded local paths/IPs and full `make test` (`engine_test`, `hy4_sampling_test`, `literals_test`, `picture_cache_test`, `e2e.sh`) passing.
  - Measured `strata-ik2` runtime on `Inkling-2-Mini-UD-Q4_K_XL`: `3.6–4.8 s` cold start, all `3,072` experts + `1.22 GiB` `mtp.safetensors` + `4.47 GiB` `FP16` KV resident on GPU (`23.67 GiB` UMA), HIP graph decode path + `hipBLASLt` prompt prefill (`990.7 tok/s` median, peaking at `1,344.6 tok/s` warmup / `1,311.9 tok/s` at 16k-class tasks).

---

## 3. Verdict & Promotion Plan (Rig B Only)

1. **Multi-Model Router `:8085` -> Promote `build-strix-vk-inkling-v2` (`b13610`):**
   - `UnbiasedStrata` (`unbiased-strata`) is a single-model server and does not replace `llama-server --models-preset models.ini --models-max 1` multi-model hot-swapping across `Qwen3.8-Flash-Next-256k`, `Qwen3.8-27B`, `qwen3.6-35b`, and `gemma4-26b-a4b`.
   - Promoting `build-strix-vk-inkling-v2` (`b13610`) to `:8085` (keeping the prior `b11620` `build/` backed up for instant rollback) gives **`Qwen3.8-Flash-Next-256k` zero-regression `55.95 tok/s` MTP throughput** and delivers **`-18.8%` median latency (`-30%` on coding)** on `Inkling-2-Mini`.
2. **Dedicated Inkling-2-Mini Serving -> Use `UnbiasedStrata` (`maya-ik2` HIP `gfx1151`):**
   - Whenever Rig B is dedicated to `Inkling-2-Mini-UD-Q4_K_XL` (batch evals, heavy agentic coding runs, or standalone Inkling service), `UnbiasedStrata` (`strata-ik2` + `--drafting auto`) is the decisive winner: **2.22× prefill (`990.7` vs `445.5 tok/s`)**, **+36.7% `v3` decode / 2.03× agentic decode (`44.6` vs `22.0 tok/s`)**, **+1 agentic pass (`11/15` vs `10/15`)**, and **40%–52% less power/energy**.
3. **High-Value Backports to `strix-fork/llama.cpp`:**
   - **Multi-turn agentic prompt/KV & DSpark retention:** `strix-fork`'s decode fell from `32.9 tok/s` (`v3`) to `22.0 tok/s` (`agentic`) during multi-turn tool-call loops (`cache_prompt: false` + tool-call grammar re-evaluation), whereas `UnbiasedStrata` held `44.6 tok/s`.
   - **RDNA 3.5 Prefill GEMM (`hipBLASLt` / WMMA):** Closing the `445` vs `990 tok/s` prefill gap (`2.22×`) in Vulkan RADV would cut long-context prompt time in half across looped MoEs.
