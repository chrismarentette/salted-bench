## Part 1: Salted Quality & Wall-Clock Benchmark (`8 GB` VRAM + `16 GB` DDR5)

### Methodology (Anti-Contamination)
- **No static textbook prompts:** All **16 tasks** (`SEED = 20261124`) were generated programmatically with randomized hex opcodes, prime moduli, custom radix alphabets, salted overlap/cooldown rules, conflicting overrides, negative formatting constraints, and randomized `4k–24k`-token log/policy streams.
- **Automated deterministic grading:** Hidden Python unit tests (`unittest`-equivalent assertions), exact symbolic answers, strict raw-JSON schema checks, and regex/word-count constraint verification.
- **Fair runtime controls:** Prompt prefix caching disabled (`cache_prompt: false`); each model run in warm blocks using its native recommended samplers (`Inkling`: `temp=1.0, top_p=1.0, top_k=0, min_p=0.0, reasoning_effort=low`; `Qwen3.6`: `temp=1.0, top_p=0.95, top_k=20, presence_penalty=1.5`). `Qwen3.6` was given an **8,192-token** output cap; `Inkling (low)` ran at **4,096** (plus an `8,192` retry on its single failure).

### Overall Scoreboard (`16` Tasks)

| Metric | `Inkling-2-Mini AD-IQ3_XXS` (`low`) | `Qwen3.6-35B-A3B GSQ` (`q2_0`) |
|---|---|---|
| **Pass@1 Overall** | **`15 / 16` (`93.8%`)** | `11 / 16` (`68.8%`) |
| **Truncated (`finish_reason=length`)** | `1 / 16` (`0` content bugs aside from `code_log_compact`) | `5 / 16` (hit `8,192` tok cap mid-thought) |
| **Total Suite Wall Time** | **`840.4s` (~14.0 min)** | `1,763.6s` (~29.4 min) |
| **Median Wall Time / Task** | **`31.38s`** (`p90: 103.19s`) | `110.92s` (`p90: 178.67s`) |
| **Total Output Tokens** | **`19,905`** (median `675`) | `73,879` (median `3,400` — **3.7×** more) |
| **Median Speed (`pp` / `tg`)** | `167.0` (max `323.3`) / `29.6 t/s` | `357.4` (max `468.7`) / `45.5 t/s` |
| **Peak VRAM** | `6,812–6,942 MiB` | `6,761–6,893 MiB` |

### Category Breakdown & Per-Task Highlights

| Category | `Inkling (low)` | `Qwen3.6 (default thinking)` | Notes |
|---|---|---|---|
| **Twisted Coding (`4` tasks)** | **`3/4`** (med `79.9s`) | `2/4` (med `170.2s`) | `Inkling` passed `vm_salt` (`36.0s` vs `135.6s`), `sched_penalty` (`120.8s` vs `8k` timeout), and `bug_hunt` (`39.0s` vs `173.8s`); both failed `log_compact` (`Inkling` finished cleanly in `142.3s` on `8k` retry with a real transitive-alias bug). |
| **Multi-Step Symbolic Reasoning (`4` tasks)** | **`4/4`** (med `46.9s`) | `1/4` (med `179.2s`) | `Inkling` solved all 4 (`16.4s–103.2s`); `Qwen3.6` burned all `8,192` tokens thinking on 3 of 4 (`state_chain`, `graph_constraints`, `base_checksum`). |
| **Strict Schema & Negative Constraints (`4` tasks)** | **`4/4`** (med `22.6s`) | `4/4` (med `53.1s`) | Both hit `100%` accuracy; `Inkling` finished **2.3× faster** wall clock due to `4–5×` fewer thinking tokens (`~530–680` vs `~1.7k–3.5k`). |
| **Long Context `4k–24k` (`4` tasks)** | **`4/4`** (med `49.3s`) | `4/4` (med `72.1s`) | Both aced `4k` multi-hop/diff and `16k–24k` state-mutation + policy-contradiction needles; `Inkling` stayed faster wall-clock (`87.3s` vs `140.3s` on state mutation) despite lower raw `pp t/s` (`323` vs `442 t/s`). |

### Ablations (Effort & Thinking Toggle)
- **`Inkling` `reasoning_effort="medium"` on Coding (`4` tasks, `8k` cap):** Scored the same **`3/4`** (`code_log_compact` hit `8,192` cap in `283.1s`), while median latency jumped **`167.5s` vs `79.9s` (`~2.1–3.5×` slower)**—`low` is the clear efficiency sweet spot on `IQ3_XXS`.
- **`Qwen3.6` `enable_thinking=False` on its `5` Overthought Failures:** Solved **`2/2` coding failures fast (`22.6s` & `26.4s`)**, actually beating `Inkling` on `code_log_compact`, but dropped to **`0/3`** on multi-step symbolic reasoning (`1.3s–25.3s`).

### Bottom Line
Even though `Qwen3.6` (`q2_0`) decodes **~1.5× faster in raw `tok/s`** (`45.5` vs `29.6 t/s`) and prefills **~1.4–1.8× faster** (`357–469` vs `167–323 t/s`), **`Inkling-2-Mini (IQ3_XXS, effort=low)` delivered higher accuracy (`93.8%` vs `68.8%`) in `3.5×` less median wall-clock time (`31.4s` vs `110.9s`)** because controlled reasoning avoided the `3.4k–8.2k`-token thinking spiral.

---

## Part 2: Brief Engine-Agnostic Tuning Lessons (Looped MoE on Constrained VRAM)

If you are deploying or optimizing a **looped MoE** (where middle layers execute `N` passes over shared physical expert tensors) with **partial VRAM + CPU/RAM offloading** on an `8–12 GB` GPU:

1. **Support Indexed-Bias FlashAttention (`kq_b != nullptr`) + Fused Loop Glue Kernels:**
   Many engines fall back to unfused attention whenever relative-position/indexed attention bias is present, spiking prompt scratch VRAM and crippling prefill even when FlashAttention is enabled. Enabling indexed-bias FA kernels is mandatory for reasonable compute footprints and TTFT.
2. **Deduplicate Shared Physical Expert Layers in the VRAM Expert Cache:**
   Track cache residency, LRU/LFU counters, and slot budgets per **unique physical expert tensor**, not per **logical loop pass**. Counting `24` physical layers looped `3×` as `48` independent layers cuts effective VRAM cache coverage in half and destroys cross-pass hit reuse. Sync lookup maps across all pass aliases on each fill/eviction.
3. **Never Share One Staging Buffer Across Loop Passes in the Graph Scheduler:**
   When a prefill op-offload path re-reads the same host expert tensor across passes `1..N`, reusing a single global copy handle extends its buffer lifetime across the entire loop (`12+` layers pinned simultaneously -> multi-GB compute OOM). Allocate per-split staging descriptors so the allocator frees and reuses one buffer between layers (`~600–700 MiB` instead of `5.7+ GiB`).
4. **Separate Large-Batch Prefill (`ubatch >= 256–512`) from Small-Batch Decode Caching, and Mind Hybrid CPU-Fallback Thresholds:**
   - **Preferred (with per-split staging):** Run large prefill microbatches (`ubatch = 256` or `512`) through device-to-device cache copies + GPU offload without evicting the hot decode expert cache.
   - **Gotcha if clamping `ubatch`:** If an engine's GPU op-offload threshold equals its MoE cache ceiling (e.g., both at `32`), setting `ubatch = 32` disables CPU miss offloading while thrashing the cache over PCIe (`TB`-scale churn). On unpatched schedulers, stepping just below the threshold (`ubatch = 31`) restores hybrid CPU miss compute (`~90–95%` of misses on CPU, `5–10%` cache refresh) and eliminates PCIe saturation.
5. **Balance Decode Cache (`~25%+` Physical Coverage) vs. VRAM Headroom:**
   - On unpinned `mmap` + `8 GB` VRAM, setting `cpu_share = 0.0` saturates PCIe on misses (`~11–13 t/s`), while `1.0` freezes the cache on cold turn-0 experts (`~14–16 t/s`); **`0.90–0.95`** keeps LRU adapting while avoiding PCIe upload stalls (`~22–30 t/s`).
   - Reserve **`~900–1,000+ MiB` free VRAM** (`~6.8–6.9 GiB` ceiling on an `8 GB` card) for CUDA graphs, desktop/compositor overhead, and full-context KV cache (`q8_0` KV fits comfortably at `64k` when paired with SWA layers).
6. **Tune `reasoning_effort` Before Micro-Optimizing Kernels:**
   On reasoning-capable MoEs, reducing `reasoning_effort` (`low` vs `medium`/unbounded) often buys a **2×–4× wall-clock latency win** with zero hardware changes—bigger than any kernel-level `tok/s` tweak.
