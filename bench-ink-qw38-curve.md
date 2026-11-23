# Continuous Reasoning Curve & Cross-Hardware Delta (`bench-ink-qw38-curve`)

## 1. Baseline 16-Task (`v1`) & Full `v3` (`28`-Task) Curves
| Run ID | Host | Model | Quant | Effort | v1 (16t) | Full Score | Trunc | Med (s) | Mean (s) | Stdev (s) | CV | TTFT med (ms) | PP (t/s) | TG (t/s) | Tok Eff | Sys W / Wh |
|---|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `p1-Rig A-qwen36-gsq-q2_0` | Rig A | Qwen3.6-35B-A3B-GSQ | q2_0 | default | 11/16 (68.8%) | 11/16 (68.8%) | 5 | 110.92 | 110.23 | 62.37 | 0.566 | 1081.4 | 357.4 | 45.5 | None | — |
| `p2-Rig B-ink2-udq4kxl-low-dspark` | Rig B | Inkling-2-Mini | UD-Q4_K_XL | low | 16/16 (100.0%) | 16/16 (100.0%) | 0 | 32.24 | 53.93 | 60.03 | 1.113 | 889.6 | 432.2 | 34.5 | None | — |
| `p2-Rig B-ink2-udq4kxl-low` | Rig B | Inkling-2-Mini | UD-Q4_K_XL | low | 15/16 (93.8%) | 15/16 (93.8%) | 0 | 32.64 | 45.6 | 40.24 | 0.883 | 843.2 | 456.0 | 29.6 | None | — |
| `p2-Rig B-qw38-27b-udq5kxl-low-mtp` | Rig B | Qwen3.8-27B | UD-Q5_K_XL | low | 15/16 (93.8%) | 15/16 (93.8%) | 0 | 47.36 | 53.43 | 33.74 | 0.631 | 1288.4 | 300.4 | 31.3 | None | — |
| `p2-Rig B-qw38-27b-udq5kxl-xhigh-mtp` | Rig B | Qwen3.8-27B | UD-Q5_K_XL | xhigh | 13/16 (81.2%) | 13/16 (81.2%) | 3 | 58.15 | 153.4 | 190.38 | 1.241 | 1310.4 | 306.9 | 25.4 | None | — |
| `p2-Rig B-qwen36-35b-q5km-nothink` | Rig B | Qwen3.6-35B-A3B | Q5_K_M | nothink | 9/16 (56.2%) | 9/16 (56.2%) | 0 | 2.63 | 10.8 | 22.82 | 2.113 | 263.0 | 1373.6 | 55.5 | 1.0 | — |
| `p2-Rig B-qwen36-35b-q5km` | Rig B | Qwen3.6-35B-A3B | Q5_K_M | default | 9/16 (56.2%) | 9/16 (56.2%) | 7 | 82.89 | 88.94 | 56.7 | 0.637 | 267.0 | 1351.6 | 56.0 | 0.003 | — |
| `p2-Rig B-qwflash-ud-iq3xxs-low-mtp` | Rig B | Qwen3.8-Flash-Next | UD-IQ3_XXS | low | 16/16 (100.0%) | 26/28 (92.9%) | 1 | 18.17 | 32.23 | 35.64 | 1.106 | 968.0 | 415.8 | 56.4 | 0.207 | — |
| `p2-Rig B-qwflash-ud-q4kxl-low-mtp` | Rig B | Qwen3.8-Flash-Next | UD-Q4_K_XL | low | 14/16 (87.5%) | 25/28 (89.3%) | 1 | 20.87 | 36.6 | 44.31 | 1.211 | 769.4 | 507.5 | 51.0 | 0.186 | — |
| `v3-Rig A-gemma4-12b-qat-mtp` | Rig A | Gemma-4-12B-QAT | Q4_0 (QAT) | think | 10/16 (62.5%) | 17/28 (60.7%) | 8 | 59.84 | 100.8 | 87.89 | 0.872 | 564.1 | 666.5 | 39.0 | 0.045 | 129.12W / 33.877Wh |
| `v3-Rig A-ink2-iq3xxs-low` | Rig A | Inkling-2-Mini-IQ3 | AD-IQ3_XXS | low | 16/16 (100.0%) | 26/28 (92.9%) | 1 | 37.34 | 66.62 | 69.21 | 1.039 | 2585.5 | 161.1 | 27.2 | 0.141 | — |
| `v3-Rig B-ink2-udq4kxl-low-dspark-frontier` | Rig B | Inkling-2-Mini | UD-Q4_K_XL | low | — | 3/4 (75.0%) | 1 | 199.58 | 199.04 | 56.73 | 0.285 | 1596.6 | 465.7 | 29.7 | 0.116 | — |
| `v3-Rig B-ink2-udq4kxl-low-dspark` | Rig B | Inkling-2-Mini | UD-Q4_K_XL | low | 16/16 (100.0%) | 27/28 (96.4%) | 1 | 34.02 | 67.66 | 77.64 | 1.148 | 874.8 | 430.7 | 33.0 | 0.142 | — |
| `v3-Rig B-qw38-27b-udq5kxl-low-mtp-frontier` | Rig B | Qwen3.8-27B | UD-Q5_K_XL | low | — | 2/4 (50.0%) | 2 | 234.39 | 236.26 | 50.32 | 0.213 | 1936.5 | 368.4 | 29.2 | 0.063 | — |
| `v3-Rig B-qw38-27b-udq5kxl-low-mtp` | Rig B | Qwen3.8-27B | UD-Q5_K_XL | low | 15/16 (93.8%) | 25/28 (89.3%) | 2 | 46.12 | 69.8 | 76.74 | 1.099 | 1289.5 | 305.9 | 30.5 | 0.156 | — |
| `v3-Rig B-qwen36-35b-q5km-frontier` | Rig B | Qwen3.6-35B-A3B | Q5_K_M | default | — | 0/4 (0.0%) | 4 | 148.37 | 156.72 | 17.37 | 0.111 | 388.3 | 1750.9 | 55.3 | 0.0 | — |
| `v3-Rig B-qwen36-35b-q5km-nothink` | Rig B | Qwen3.6-35B-A3B | Q5_K_M | nothink | 9/16 (56.2%) | 19/28 (67.9%) | 1 | 3.72 | 17.03 | 32.69 | 1.919 | 271.8 | 1366.6 | 55.7 | 1.0 | — |
| `v3-Rig B-qwflash-ud-iq3xxs-med-mtp` | Rig B | Qwen3.8-Flash-Next | UD-IQ3_XXS | medium | 15/16 (93.8%) | 26/28 (92.9%) | 1 | 18.56 | 38.09 | 41.4 | 1.087 | 754.7 | 505.4 | 54.9 | 0.151 | 133.92W / 39.68Wh |
| `v3-Rig B-qwflash-ud-q4kxl-med-mtp` | Rig B | Qwen3.8-Flash-Next | UD-Q4_K_XL | medium | 16/16 (100.0%) | 26/28 (92.9%) | 2 | 20.66 | 41.08 | 49.84 | 1.213 | 577.4 | 631.3 | 51.9 | 0.136 | 125.47W / 40.097Wh |

## 2. Extended-v4 (`6`-Task: Epistemic Honesty, Surgical Diff, 2-Turn Recovery)
| Run ID | Model | Effort | Score | Med (s) | Stdev (s) | TTFT med (ms) | TG (t/s) | Tok Eff | Sys W / Wh |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| `extv4-Rig B-ink2-udq4kxl-low-dspark` | Inkling-2-Mini | low | 6/6 (100.0%) | 14.12 | 19.4 | 686.8 | 32.9 | 0.081 | 133.51W / 4.425Wh |
| `extv4-Rig B-ink2-udq4kxl-med-dspark` | Inkling-2-Mini | medium | 5/6 (83.3%) | 17.26 | 21.06 | 691.0 | 33.0 | 0.045 | 133.63W / 5.134Wh |
| `extv4-Rig B-qwflash-ud-iq3xxs-low-mtp` | Qwen3.8-Flash-Next | low | 5/6 (83.3%) | 5.08 | 11.74 | 848.6 | 60.0 | 0.176 | 133.0W / 2.056Wh |
| `extv4-Rig B-qwflash-ud-iq3xxs-med-mtp` | Qwen3.8-Flash-Next | medium | 5/6 (83.3%) | 5.22 | 2.76 | 657.5 | 54.8 | 0.248 | 133.79W / 1.307Wh |
| `extv4-Rig B-qwflash-ud-q4kxl-low-mtp` | Qwen3.8-Flash-Next | low | 5/6 (83.3%) | 5.55 | 2.52 | 1095.4 | 51.5 | 0.267 | 121.98W / 1.215Wh |
| `extv4-Rig B-qwflash-ud-q4kxl-med-mtp` | Qwen3.8-Flash-Next | medium | 5/6 (83.3%) | 6.46 | 3.31 | 512.0 | 52.5 | 0.19 | 128.18W / 1.531Wh |

## 3. Cross-Hardware Performance Delta (Node 1 Split PCIe/DDR5 vs Node 2 Resident UMA)
| Metric | Node 1 (`AD-IQ3_XXS`, `--cpu-moe`) | Node 2 (`UD-Q4_K_XL + DSpark`, UMA) | Delta / Factor |
|---|---|---|---|
| Pass Rate (`v3` 28t) | 26/28 (92.9%) | 27/28 (96.4%) | +1 task on UMA Q4 |
| Prefill (`pp_tps` med) | 161.1 t/s | 430.7 t/s | **2.67x faster on UMA** |
| TTFT median | 2585.5 ms | 874.8 ms | **2.96x latency penalty on Node 1** |
| Decode (`tg_tps` med) | 27.2 t/s | 33.0 t/s | **1.21x faster on UMA** |
| Wall median / mean | 37.34s / 66.62s | 34.02s / 67.66s | Stdev 69.21s vs 77.64s |

- **Inkling-2-Mini 3x Looped-Layer Bottleneck:** Because Inkling's middle 12 MoE layers execute **3x per token**, every uncached expert read spilled to DDR5 crosses PCIe 4.0 **3x per token**. That inflates median TTFT **2.96x** (`2585.5 ms` -> `874.8 ms`) and cuts prefill throughput **2.67x** (`161.1` vs `430.7 t/s`), whereas `--moe-cache-mib` keeps decode closer (`27.2` vs `33.0 t/s`).
- **Qwen3.6-35B-A3B (`v1` 16t):** Node 1 `q2_0 --cpu-moe` scored `11/16 (68.8%)` (`pp=357.4 t/s`, `tg=45.5 t/s`, `ttft=1081.4 ms`) vs Node 2 `Q5_K_M` resident UMA `9/16 (56.2%)` (`pp=1351.6 t/s`, `tg=56.0 t/s`, `ttft=267.0 ms`).
