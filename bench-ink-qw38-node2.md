## Phase 2: Strix Halo Unified Memory (`128 GB` UMA / Vulkan RADV `gfx1151`) — `Inkling-2-Mini` (`UD-Q4_K_XL` + DSpark) vs `Qwen3.8-27B` (`UD-Q5_K_XL` + MTP)

### Methodology & Controls
- **Hardware & Runtime:** AMD Strix Halo (`Radeon 8060S Graphics`, `gfx1151`, ~`121 GB` unified LPDDR5X-8000, Mesa RADV Vulkan), staged in an isolated worktree (`b1070-inkling-eval`, commit `7b5e70ab5`) on loopback `:8099` while production `:8085` (`build-strix-vk-b1066`) stayed unloaded/untouched.
- **Anti-Contamination Suite (`SEED = 20261124`):** Identical 16-task PRNG-salted suite (`salted-eval.py`, `cache_prompt: false`, warmup discarded, `max_tokens = 8192`, `--parallel 1`, `-fa on -ctk q8_0 -ctv q8_0 -b 2048 -ub 2048 -ngl 999`).
- **Native Samplers & Flags:**
  - `Inkling-2-Mini-UD-Q4_K_XL` (`17.6 GB`): `temp=1.0, top_p=1.0, top_k=0, min_p=0.0`, `reasoning_effort="low"` (plus `medium` ablation); DSpark arm adds `dspark-Inkling-2-Mini-Q8_0.gguf` (`--spec-type draft-dspark --spec-draft-n-max 3 -ctkd q8_0 -ctvd q8_0`).
  - `Qwen3.8-27B-UD-Q5_K_XL` (`20.0 GB`): `temp=1.0, top_p=0.95, top_k=20, min_p=0.0, presence_penalty=0.0`, `mtp-Qwen3.8-27B-Q4_0.gguf` (`--spec-type draft-mtp --spec-draft-n-max 4`), run at both `reasoning_effort="low"` and default `xhigh`.

### 1. Overall Scoreboard (`16` Salted Tasks, `SEED = 20261124`)

| Metric | `Inkling-2-Mini` + DSpark (`low`) | `Inkling-2-Mini` Standalone (`low`) | `Qwen3.8-27B` + MTP (`low`) | `Qwen3.8-27B` + MTP (`xhigh`) |
|---|---|---|---|---|
| **Pass@1 Overall** | **`16 / 16` (`100.0%`)** | `15 / 16` (`93.8%`) | `15 / 16` (`93.8%`) | `13 / 16` (`81.2%`) |
| **Truncated (`finish_reason=length`)** | `0 / 16` | `0 / 16` | `0 / 16` | `3 / 16` (`8,192` cap in Coding) |
| **Total Suite Wall Time** | `862.8s` (`14.38 min`) | **`729.6s` (`12.16 min`)** | `855.0s` (`14.25 min`) | `2,454.6s` (`40.91 min`) |
| **Median Wall Time / Task** | **`32.24s`** (`p90: 151.88s`) | `32.64s` (`p90: 114.96s`) | `47.36s` (`p90: 110.89s`) | `58.15s` (`p90: 477.37s`) |
| **Total Output Tokens (Median)** | `22,057` (`693`) | **`18,433` (`601`)** | `21,058` (`1,026`) | `42,667` (`938`) |
| **Median `pp` / `tg` Speed** | `432.2` / **`34.5 t/s`** | **`456.0`** / `29.6 t/s` | `300.4` / `31.3 t/s` | `306.9` / `25.4 t/s` |
| **Draft Acceptance** | `50.3%` (`13,264 / 26,346`) | — | **`87.5%` (`15,716 / 17,959`)** | `72.9%` (`23,910 / 32,795`) |
| **Peak GTT (`avail` Floor)** | `24,630 MiB` (`91.2 GiB`) | `22,356 MiB` (`93.9 GiB`) | `25,173 MiB` (`85.6 GiB`) | `25,188 MiB` (`85.5 GiB`) |

### 2. Category Breakdown & Ablations

| Category (`4` tasks each) | `Inkling` + DSpark (`low`) | `Inkling` Standalone (`low`) | `Qwen3.8-27B` + MTP (`low`) | `Qwen3.8-27B` + MTP (`xhigh`) |
|---|---|---|---|---|
| **Twisted Coding** | **`4/4`** (med `100.44s`) | **`4/4`** (med `70.82s`) | **`4/4`** (med `87.98s`) | `1/4` (med `470.72s`, `3` trunc) |
| **Symbolic Reasoning** | **`4/4`** (med `27.86s`) | `3/4` (med `30.11s`) | `3/4` (med `45.27s`) | **`4/4`** (med `54.48s`) |
| **Strict Schema & Negative** | **`4/4`** (med `16.27s`) | `4/4` (med `17.68s`) | `4/4` (med `24.86s`) | `4/4` (med `27.48s`) |
| **Long Context (`4k–24k`)** | **`4/4`** (med `32.28s`) | `4/4` (med `34.55s`) | `4/4` (med `45.41s`) | `4/4` (med `46.89s`) |

- **`Inkling-2-Mini + DSpark (medium)` Ablation (`8` Coding + Reasoning tasks):** Dropped to **`7 / 8` (`87.5%`)** (`code_log_compact` truncated at `8,192` tokens in `312.3s`), median wall doubled to **`67.93s`** vs `~32s` at `low` (`28,727` tok, `49.8%` DSpark acceptance). Confirms `reasoning_effort="low"` is the Pareto sweet spot on both `Rig A` and `Rig B`.
- **`Qwen3.8-27B` Effort Sensitivity (`low` vs `xhigh`):** `xhigh` lost **`-12.6%` accuracy** (`81.2%` vs `93.8%`) from three `8,192`-token coding spirals (`464s–506s` each) and dragged MTP acceptance down (`87.5% -> 72.9%`, `31.3 -> 25.4 t/s`), taking `40.91 min` vs `14.25 min`.
- **`Inkling-2-Mini` v2 (`24`-Task Suite, `Rig A` check with `AD-IQ3_XXS`):** Passed **`23 / 24` (`95.8%`)**, acing all `4/4` Agent Tool tasks (`tool_search_first`, `tool_honest_failure`, `tool_surgical_patch`, `tool_kanban_triage`, med `7.43s`) and `4/4` Ops/Voice tasks (`med 48.89s`).
- **Blind Refusal Eval (`Rig A`, `safe_dataset.jsonl` `--per-category 1`, `32` unsafe + `4` benign lookalikes):**
  - `Inkling-2-Mini AD-IQ3_XXS (low)`: **`0 / 32` refusals (`0.0%`), `0` pivots, `32 / 32` (`100.0%`) strict compliance**, `0%` benign false-refusal (med `15.55s`).
  - `Qwen3.6-35B-A3B-GSQ q2_0 (--disable-thinking)`: **`3 / 32` hard refusals (`9.4%`) + `20 / 32` soft pivots (`71.9%` combined refusal/pivot)**, **`9 / 32` (`28.1%`)** strict compliance (`5.3%` on danger `4–5`), `0%` benign false-refusal.

### 3. Strix Halo (`Rig B`) / Vulkan RADV Engine Findings
1. **Why Phase 1 (`Rig A`) Fixes Didn't Transfer to `Rig B`:**
   - Phase 1 solved `llama_moe_cache` physical-layer deduplication and `ggml-backend` per-split host-weight staging for discrete `8 GB` CUDA (`--cpu-moe` + `-ub`). On `Rig B`'s `128 GB` UMA with `-ngl 999`, every tensor is GPU-resident and `llama_moe_cache` is never invoked.
   - Instead, porting `inkling2_loop` onto `strix-fork/strix-llama.cpp` hit two `Rig B`-specific bottlenecks:
     1. **Unsloth Loop Schema Compatibility:** Unsloth exports `num_loops`, `loop_res_query`, `loop_kv_res_query`, and `blk.%d.loop_{attn,k,v,mlp}_{down,gain,forget}` on arch `inkling2` rather than `inkling2_loop`. Adding dual-schema loading in `src/llama-arch.*`, `src/llama-model.*`, `src/llama-context.cpp`, and `src/models/{inkling2.cpp,inkling2-loop.cpp}` keeps both upstream and Unsloth GGUFs working.
     2. **Vulkan RADV `194`-Split CPU Fallback in `GGML_OP_SCALE`:** `GGML_SCHED_DEBUG=2` caught `LLM_FFN_SITU` kicking `96` per-layer `SCALE` ops back to CPU (`194` graph splits) due to missing strided-view support in `ggml/src/ggml-vulkan/vulkan-shaders/scale.comp` and `ggml-vulkan.cpp`. Fixing strided `SCALE` collapsed splits **`194 -> 2`**, passed `test-backend-ops test -o SCALE` (`7/7`), and raised standalone `Inkling-2-Mini` decode **`22.5 -> 31.71 t/s`** (`655.65 t/s pp512`, `35.6–37.7 t/s` with DSpark on short prompts).
2. **`Qwen3.8-27B` `b1066` Parity & Promotion Safety (`build-strix-vk-inkling`):**
   - `llama-bench` AB parity (`b1066` vs `build-strix-vk-inkling` on `Qwen3.8-27B-UD-Q5_K_XL` `-ngl 999 -fa 1`):
     - `pp512`: `513.88 ± 1.67` (`b1066`) vs **`519.77 ± 1.11`** (`inkling`)
     - `pp2048`: `504.19 ± 2.42` (`b1066`) vs **`507.57 ± 8.03`** (`inkling`)
     - `tg64`: `10.74 ± 0.01` (`b1066`) vs `10.65 ± 0.01` (`inkling`)
   - Multimodal + draft co-residence (`Inkling-2-Mini-UD-Q4_K_XL` + `dspark-Inkling-2-Mini-Q8_0` + `mmproj-F16.gguf`) boots cleanly together at `~25.6 GiB` GTT (`91+ GiB` RAM still free).
   - **Verdict:** Zero `b1066` shader regression and clean `mmproj` + `dspark`/`mtp` stability; safe to promote `build-strix-vk-inkling` (`7b5e70ab5`) to `:8085` once the pending >64k DSpark curve and `Rig B` `Qwen3.6-35B Q5_K_M` runs complete.

### 4. Empirical DSpark >64k Context Curve & `Qwen3.6-35B-A3B Q5_K_M` (`Rig B`)

#### A. DSpark vs. Standalone Across `1.4k -> 95k` Context (`Inkling-2-Mini-UD-Q4_K_XL`)
- **Architecture:** `dspark-Inkling-2-Mini-Q8_0.gguf` has `3` layers with `dflash.attention.sliding_window = 1024` and `sliding_window_pattern = [True, False, True]` (`layer 1` is full global attention), fed across `target_layers = [5, 23, 41, 48]`.
- **Measured Sweep (`Rig B`, `q8_0` KV, `n_max=3`, `max_tokens=256`, `SEED = 20261124`):**

| Prompt Tokens | DSpark Accept (`acc / n`) | DSpark `pp` / `tg` | Standalone `pp` / `tg` | Net `tg` Delta | Extra GTT |
|---:|---:|---:|---:|---:|---:|
| **`1,386` (~1.4k)** | `40.1%` (`139/347`) | `584.5` / `27.59 t/s` | `581.1` / **`29.43 t/s`** | `-6.3%` | `+2,217 MiB` |
| **`32,309` (~32k)** | `47.3%` (`149/315`) | `404.2` / `25.87 t/s` | `418.6` / **`26.61 t/s`** | `-2.8%` | `+2,220 MiB` |
| **`70,288` (~70k)** | `43.2%` (`143/331`) | `292.6` / `21.18 t/s` | `308.6` / **`23.77 t/s`** | `-10.9%` | `+2,222 MiB` |
| **`94,901` (~95k)** | **`46.9%`** (`149/318`) | `246.8` / `20.49 t/s` | `264.5` / **`22.37 t/s`** | `-8.4%` | `+2,222 MiB` |

- **Verdict on the >64k Claim:** Acceptance does **not** fall off a cliff beyond 64k (`46.9%` at `94.9k` vs `40.1%` at `1.4k`). Rather, DSpark costs `+2.2 GiB` steady-state memory and only outperforms standalone when acceptance exceeds **`~50%+`** (such as the `50.3%` average across Phase 2's 16-task suite, where DSpark won **`+16.6%` median `tg`**, `34.5` vs `29.6 t/s`). Below `~48%` acceptance — or past `64k` where scanning `70k–95k` KV across `Inkling`'s 8 global layers (`1+3` verification tokens) plus DSpark's global layer 1 raises step cost — standalone decode is `3%–11%` faster.

#### B. `Qwen3.6-35B-A3B` Fatter `Q5_K_M` (`24 GB`, `Rig B`) vs. `Inkling` & `Qwen3.8-27B`
- **Overall (`16` tasks, `SEED = 20261124`, `temp=0.6, presence_penalty=1.5`, `max_tokens=8192`):**
  - **Pass@1:** **`9 / 16` (`56.2%`)** (`7` truncations at `8,192` tokens)
  - **Wall Clock:** `23.72 min` (`82.89s` med, `147.34s` p90), `77,888` total tokens (`4,640` med)
  - **Raw Hardware Speed:** **`1,351.6 t/s` med prefill (`2,703.5 t/s` max)** / **`56.0 t/s` med decode** (`51.0–51.6 t/s` at 16k)
  - **Category Split:** Coding **`0/4`** (`0.0%`, `4/4` truncated at `8,192`), Reasoning **`1/4`** (`25.0%`, `3/4` truncated), Strict Schema **`4/4`** (`100%`, med `39.45s`), Long Context **`4/4`** (`100%`, med `25.26s`).
  - **Takeaway:** On `Rig B`'s 128 GB UMA, Gated DeltaNet + `3.5B` active `Q5_K_M` delivers blazing raw prefill/decode (`1,352` / `56.0 t/s`), yet unbounded thinking spirals caused `0/4` coding and `1/4` reasoning under an `8,192`-token cap — beaten decisively in both accuracy and wall time by `Inkling-2-Mini (+DSpark, low)` (`100%`, `32.24s` med) and `Qwen3.8-27B (+MTP, low)` (`93.8%`, `47.36s` med).

#### C. `v3` `frontier_ceiling` Calibration (`Rig A` `Inkling-2-Mini-IQ3`, `low`) & Wave 3
- Added `--suite v3` (`28` tasks) / `--subset frontier` (`4` tasks: `front_bilevel_dp`, `front_nonlocal_bijection_10`, `front_16k_rollback_audit`, `front_ood_affine_tx_taint`) to break `100%` benchmark saturation: `Inkling-2-Mini-IQ3` (`Rig A`, `low`) scored **`2 / 4` (`50.0%`)** (passed `front_bilevel_dp` & `front_16k_rollback_audit`; failed `front_nonlocal_bijection_10` on `8,192`-tok length exhaustion and `front_ood_affine_tx_taint` on logic error).
- Wave 3 queued on `Rig B :8099`: `Qwen3.6-35B Q5_K_M (nothink)`, `Gemma-4-26B-A4B-it (UD-Q5_K_XL + MTP)`, `Qwen3.8-Flash-Next (UD-IQ3_XXS + MTP, low)`, `Qwen3.8-Flash-Next (UD-Q4_K_XL + MTP, low)`, and `v3 --subset frontier` across `Inkling`, `Qwen3.8-27B`, and `Qwen3.6-35B`.
