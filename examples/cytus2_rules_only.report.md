# 遊戲效能瓶頸初判報告

- Trace：`cytus2_a.pftrace`
- 目標 process：`com.rayark.cytus2`（auto: most frames (0 FrameTimeline frames, 3602 queueBuffer calls); next: com.android.systemui (15 FrameTimeline frames, 15 queueBuffer calls)）
- 裝置 build：`samsung/e2qzhx/e2q:15/AP3A.240905.015.A2/S9260ZHS4BYG1:user/release-keys`，Android SDK 35
- 分析區間：29.9 s

> 這是**初判**，不是結論：數字由程式從 trace 萃取，判讀由 LLM 與規則各自提出，每個被引用的數字都經過程式核對（grounding）。請以 Perfetto UI 驗證後再下結論。

## 判讀結果

| 判讀者 | 瓶頸類別 | 信心 | 引用數字核對 |
|---|---|---|---|
| LLM | 未執行（--no-llm） | — | — |
| 規則 baseline | **thermal 降頻** (`thermal_throttling`) | medium | 6/6 |

### 判斷依據（規則 baseline）

- 1% low 12.6 fps vs average 109.7.  
  `frames.fps_1pct_low` = 12.60719812909747 ✅
- FPS fell from 114.2 (early) to 93.2 (late).  
  `frames.segments.2.avg_fps` = 93.22409516097042 ✅
- Android thermal status reached 1.  
  `thermal.thermal_status_max` = 1.0 ✅
- mid max frequency fell to 0.65x of early.  
  `cpufreq.clusters.1.late_vs_early_max_ratio` = 0.6493506493506493 ✅
- big max frequency fell to 0.71x of early.  
  `cpufreq.clusters.2.late_vs_early_max_ratio` = 0.7134146341463414 ✅
- prime max frequency fell to 0.69x of early.  
  `cpufreq.clusters.3.late_vs_early_max_ratio` = 0.689655172413793 ✅

- 下一步：Repeat after cooling the device and compare early-segment FPS.
- 下一步：Check whether frequency caps come from thermal mitigation or a game power policy.

## 指標摘要

### Frame pacing

來源：`queue_buffer`（thread:UnityMain；present 時間：`app_queue_buffer`）

| 指標 | 值 |
|---|---|
| frame 數 | 3139 |
| 平均 FPS | 109.7 |
| 1% low FPS | 12.6 |
| frame interval P50 / P90 / P99 / max (ms) | 8.35 / 9.52 / 24.37 / 372.59 |
| 慢 frame（≥1.5× 中位數）比例 | 3% |
| jank 類型 | 此 trace 無此資料（queue_buffer 來源沒有 SurfaceFlinger jank 分類） |
| 前 / 中 / 後段 FPS | 114.2 / 119.9 / 93.2 |
| 閒置空檔（>1 s，已排除） | 1 |

### 關鍵 thread

| thread | 角色 | CPU 使用率 | 等 CPU 比例 | D 狀態 (ms) | cluster 分佈 |
|---|---|---|---|---|---|
| UnityMain | game_logic | 57% | 4% | 170 | big 58%, prime 42% |
| NDK MediaCodec_ | top_cpu | 7% | 57% | 0 | mid 90%, little 10% |
| Thread-98 | top_cpu | 6% | 57% | 3 | big 96%, prime 4% |
| Thread-96 | top_cpu | 6% | 57% | 2 | big 97%, prime 3% |
| Thread-97 | top_cpu | 6% | 57% | 15 | big 97%, prime 3% |
| Thread-99 | top_cpu | 6% | 58% | 7 | big 96%, prime 4% |
| m.rayark.cytus2 | main | 1% | 39% | 0 | big 89%, prime 9%, mid 2% |

### CPU 頻率（DVFS）

| cluster | CPUs | 硬體上限 MHz | 平均 MHz | 平均/上限 | 前段→後段最高頻 |
|---|---|---|---|---|---|
| little | [0, 1] | 2266 | 1339 | 59% | 1805 → 1805 |
| mid | [5, 6] | 2957 | 882 | 30% | 2957 → 1920 |
| big | [2, 3, 4] | 3149 | 975 | 31% | 3149 → 2246 |
| prime | [7] | 3398 | 1025 | 30% | 3341 → 2304 |

### GPU

來源：`sidecar`；平均 231 MHz（硬體上限 1000），平均 busy 19%，動態上限最低 231 MHz（受限時間 98%），thermal pwrlevel 最大 13。

### Thermal

Android thermal status 最大值：1（≥ LIGHT 時間 100%）

| zone | 來源 | 起始 °C | 最高 °C | 變化 °C |
|---|---|---|---|---|
| cpuss | sidecar | 65.9 | 65.9 | -13.1 |
| gpuss | sidecar | 55.6 | 55.6 | -7.5 |
| xo-therm | sidecar | 45.5 | 45.9 | +0.4 |
| battery | sidecar | 39.2 | 39.5 | +0.3 |

CPU 頻率上限（scaling_max_freq；可能來自 thermal 或廠商遊戲策略）：

| cluster | 最低上限 / 硬體上限 | 受限時間 |
|---|---|---|
| little | 75% | 97% |
| big | 34% | 97% |
| mid | 40% | 97% |
| prime | 33% | 97% |

## 資料可用性

| 資料來源 | 有無 |
|---|---|
| frame_timeline | 有 |
| sched | 有 |
| cpufreq | 有 |
| gpufreq | 無 |
| thermal | 無 |
| sidecar | 有 |

ftrace 啟用失敗：`Ftrace event unknown: power/gpu_frequency`

sidecar 時間對齊：吻合率 100%（平移 ±1–2 s 的基準 72%）
