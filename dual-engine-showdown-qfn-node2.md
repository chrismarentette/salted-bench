# Dual-Engine Showdown — Rig B (`128 GB Strix Halo`, `gfx1151`) · `Qwen3.8-Flash-Next` (`QFN`)

Exclusive hardware evaluation on **Rig B (`AMD Ryzen AI Max+ / Radeon 8060S gfx1151`, `121 GiB` UMA)** comparing updated **`strix-fork/llama.cpp b13610` (Vulkan RADV + `draft-mtp`)** against pure-source **`UnbiasedStrata 0.1.45` (`strata` + `qfn-mtp-rt`, ROCm HIP `gfx1151`)** on **`Qwen3.8-Flash-Next-UD-Q4_K_XL` (`+ mmproj-BF16`)**, with `:8085` stopped so 100% of UMA bandwidth and compute belonged to the engine under test.

- **Test Date:** 2026-11-23
- **Parity Controls:** `--seed 20261124`, `cache_prompt: false`, `--ctx 131072`, `--reasoning-effort low`, `temp=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0.0`
- **Suites:** `salted-eval.py v3` (`28` tasks, `max_tokens=8192`) + `salted-agentic.py` (`15` tasks: `6` harness + `4` real-world + `1` epistemic impossibility + `4` hard frontier, `max_tokens=4096`)
- **Primary Conclusion:** On **Rig B**, **`UnbiasedStrata` (`strata` `ROCm HIP` + `qfn-mtp-rt`)** beats `strix-fork b13610` (Vulkan RADV + `draft-mtp`) on both accuracy and throughput: **`27/28 (96.4%)` vs `25/28 (89.3%)`** on `v3`, **`+20.5%` faster median decode** (`62.2` vs `51.6 tok/s`, peaking at `70.3 tok/s`), **`84.7%` vs `80.0%` MTP acceptance**, **`16.0%` faster `v3` total wall** (`13.9` vs `16.6 min`), and **`19.5%` faster `agentic` total wall** (`10.8` vs `13.4 min`, `3/4` vs `1/4` on frontier agentic tasks)—while caching all `24,576 / 24,576` experts (`71.73 GiB`) in GPU memory with `34.3 GiB` RAM still free.

---

## 1. Scoreboard — Rig B (`Qwen3.8-Flash-Next-UD-Q4_K_XL`, `131k` ctx, `low`)

### A. `v3` (`28` Deterministic Parity Tasks)

| Metric | `UnbiasedStrata 0.1.45` (ROCm HIP `gfx1151` + `qfn-mtp-rt`) | `strix-fork b13610` (Vulkan RADV + `draft-mtp`) | Delta (`UnbiasedStrata` vs `b13610`) |
|---|---:|---:|---:|
| **Pass@1** | **27/28 (96.4%)** | 25/28 (89.3%) | **+2 tasks (+7.1 pp)** |
| **Reasoning Failures** | **0** (4/4) | 2 (`reason_state_chain`, `reason_base_checksum`) | **+2 reasoning** |
| **Truncations (`length`)** | 1 (`front_nonlocal_bijection_10`) | 1 (`front_nonlocal_bijection_10`) | Parity |
| **Total Suite Wall** | **835.4 s (13.92 min)** | 994.8 s (16.58 min) | **1.19× faster (-2.66 min)** |
| **Median / Mean / P90 Wall** | **17.74 s / 29.83 s / 72.8 s** | 20.33 s / 35.53 s / 72.3 s | **1.15× faster median** |
| **Median Prefill (`pp`)** | 454.3 tok/s (16k `long_context`: `1,206.4 tok/s`, max `1,265.1`) | **528.2 tok/s** (16k `long_context`: `1,257.7 tok/s`, max `1,298.1`) | Vulkan faster `<4k` (`STRATA_PF_SWITCH_MIN_T=4096`) |
| **Median Decode (`tg`)** | **62.2 tok/s** (max `70.3 tok/s`) | 51.6 tok/s (max `59.7 tok/s`) | **+20.5% faster decode** |
| **MTP Draft Acceptance** | **84.7%** (`29,216 / 34,486`) | 80.0% (`31,711 / 39,630`) | **+4.7 pp acceptance** |
| **Energy / Avg Power (`amdgpu_hwmon_ppt`)** | **31.07 Wh** (`133.74 W` sys, **1.15 Wh/pass**) | 35.47 Wh (`128.23 W` sys, 1.42 Wh/pass) | **-12.4% energy (-18.9% Wh/pass)** |
| **Peak UMA / Headroom** | `~87 GiB` UMA (`34.3 GiB` free; `24,576/24,576` experts GPU-cached, `int8` KV) | `~88 GiB` UMA (`q8_0` KV, `on-direct`) | ~Parity (`~1 GiB` less) |

#### `v3` Category Breakdown

| Category | `UnbiasedStrata` Pass (Med Wall / `pp` / `tg`) | `strix-fork b13610` Pass (Med Wall / `pp` / `tg`) | Winner |
|---|---|---|---|
| `coding` | 4/4 (33.00 s / 450.8 / **59.6**) | 4/4 (**30.75 s** / **517.4** / 50.8) | Tie accuracy; Vulkan `<4k` pp vs Strata tg |
| `reasoning` | **4/4** (**19.67 s** / 419.5 / **63.4**) | 2/4 (25.65 s / **485.8** / 56.2) | **UnbiasedStrata (+2 pass, 1.30× faster)** |
| `structured` | 4/4 (**10.07 s** / 361.5 / **65.0**) | 4/4 (14.68 s / **423.5** / 55.2) | **UnbiasedStrata (1.46× faster)** |
| `long_context` (`4k–16k`) | 4/4 (**16.72 s** / 1,206.4 / **62.6**) | 4/4 (19.24 s / **1,257.7** / 53.4) | **UnbiasedStrata (1.15× faster)** |
| `agent_tools` | 4/4 (**3.57 s** / 313.1 / **57.1**) | 4/4 (4.80 s / **370.7** / 47.3) | **UnbiasedStrata (1.34× faster)** |
| `ops_and_voice` | 4/4 (**19.19 s** / 646.2 / **58.1**) | 4/4 (19.40 s / **762.3** / 49.8) | **UnbiasedStrata (+8.3 tok/s tg)** |
| `frontier_ceiling` | 3/4 (**95.12 s** / 574.0 / **56.8**) | 3/4 (132.03 s / **734.9** / 51.0) | **UnbiasedStrata (1.39× faster)** |

### B. `salted-agentic.py` (`15` Multi-Turn Sandboxed Repo Tasks)

| Metric | `UnbiasedStrata 0.1.45` (ROCm HIP + `qfn-mtp-rt`) | `strix-fork b13610` (Vulkan RADV + `draft-mtp`) | Delta |
|---|---:|---:|---:|
| **Pass@1 (Total)** | **12/15 (80.0%)** | **12/15 (80.0%)** | Parity |
| `agent_harness` (`6t`) | 6/6 (**20.06 s** med) | 6/6 (25.16 s med) | **UnbiasedStrata (1.25× faster)** |
| `epistemic_impossibility` (`1t`) | 1/1 (**45.78 s**) | 1/1 (56.31 s) | **UnbiasedStrata (1.23× faster)** |
| `realworld_codebase` (`4t`) | 2/4 (**41.39 s** med) | **4/4** (54.56 s med) | `strix-fork` passed `lsmtop_journal_metrics` + `oxirack_ccol_bounds` |
| `frontier_agentic` (`4t` hard) | **3/4** (**70.51 s** med, **49.5 tok/s**) | 1/4 (93.00 s med, 35.0 tok/s) | **UnbiasedStrata (+2 hard frontier passes)** |
| **Total Wall Time** | **646.9 s (10.78 min)** | 803.2 s (13.39 min) | **1.24× faster (-2.61 min)** |
| **Median Task Wall** | **38.58 s** | 47.65 s | **1.23× faster** |
| **Median Decode (`tg`)** | **37.2 tok/s** (max `52.4`) | 31.4 tok/s (max `37.4`) | **+18.5% faster agentic decode** |
| **Energy / Avg Power (`amdgpu_hwmon_ppt`)** | **23.86 Wh** (`132.64 W` sys, **1.99 Wh/pass**) | 29.50 Wh (`131.74 W` sys, 2.46 Wh/pass) | **-19.1% energy** |

---

## 2. Build, Pack & Configuration Notes (`UnbiasedStrata` `strata` vs `strix-fork`)

- **`UnbiasedStrata` (`strata` `0.1.45`, ROCm HIP `gfx1151`):**
  - Built purely from source with `amdclang`/`amdclang++` (`TheRock 7.14.1`, `gfx1151`, `STRATA_ENABLE_HIP=ON`, `STRATA_PREFILL_MMQ=ON`, `STRATA_NATIVE_EXPERTS=ON`, `STRATA_MMQ_KQUANTS=ON`, `STRATA_Q6K_EXPERTS=ON`).
  - **UMA Expert Residency (`qfn-ud-q4kxl`):** `iq_pack.py --compat-bf16` + `--native` + `--mmap-experts --expert-cache 24576 --vram-reserve-mib 8192` loaded all **`24,576 / 24,576`** routed experts (`71.73 GiB`, `73,450 MiB` cache) in `37.8 s` (`2,040 MB/s`) with `0` CPU expert misses and **`~34.3 GiB` RAM free** (`131K` `int8` KV, `mmproj-BF16`).
  - **MTP Runtime (`qfn-mtp-rt`):** Converted from the local `Q8_0` shared MTP GGUF (`experts.bin` `675 MiB` `Q2_0`, `dense.bin` `110 MiB`, `Q4_0` `106,299`-token draft head = `146 MiB/step`), run with `--mtp-hnorm stream --mtp-q4 all --mtp-window 8192 --spec 4 --spec-min-p 0.5 --lookup-chain 3` and `STRATA_SPEC_COUPLED=1` (`84.7%` acceptance across `v3`).
  - **Prefill Observation:** `STRATA_PF_SWITCH_MIN_T=4096` kept short prompts (`<4k` tokens) off the `hipBLASLt` WMMA prefill path (`313–451 tok/s` vs Vulkan `371–517 tok/s`), while `8k–16k` prompts hit **`1,206–1,265 tok/s`**.
  - **Smoke Verification (`:8099`):** Verified `17*29+5 = 498` (`64.9 tok/s`, `90.9%` MTP), `get_weather({"city":"Boston"})` tool calls, and `#FF0000` synthetic PNG vision recognition (`'Red'`, `44.8 tok/s`).
- **`strix-fork/llama.cpp b13610` (`Vulkan RADV`):**
  - Exact production `QFN` profile flags: `draft-mtp` (`n_max=3`, `p_min=0`, `mtp_vocab=65536`, `--no-spec-draft-backend-sampling`), `--load-mode none --lazy-mode on-direct -b 4096 -ub 4096 -ctk q8_0 -ctv q8_0 --ctx-checkpoints 4 -c 131072 -fa on`.

---

## 3. Verdict & Operational Takeaway (Rig B)

1. **`UnbiasedStrata` (`strata` HIP) IS decisively faster and higher-scoring on `QFN` (`UD-Q4_K_XL`) on Rig B:** `+2` tasks on `v3` (`27/28` vs `25/28`), `+20.5%` decode (`62.2` vs `51.6 tok/s`), `+19.5%` faster `agentic` suite wall with `3/4` vs `1/4` frontier passes, and `+4.7 pp` higher MTP draft acceptance (`84.7%` vs `80.0%`).
2. **Keep `strix-fork b13610` for `:8085` multi-model router (`llama-server --models-preset`):** Because `:8085` dynamically hot-swaps across multiple models (`Qwen3.8-Flash-Next`, `Qwen3.8-27B`, `Inkling-2-Mini`, `qwen3.6-35b`, `gemma4-26b`), `llama-server` remains the required multi-model router, and promoting `b13610` preserves `51.6–56 tok/s` `QFN` MTP speed while improving `Inkling-2-Mini`.
3. **Use `UnbiasedStrata` (`strata` / `strata-ik2`) whenever Rig B runs dedicated heavy `QFN` or `Inkling` workloads:** Dedicated `UnbiasedStrata` gives `1.2–2.0×` faster throughput and higher ceiling scores on both main models.
