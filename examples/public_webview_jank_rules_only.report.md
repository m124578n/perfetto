# 遊戲效能瓶頸初判報告

- Trace：`webview_jank.pb`
- 目標 process：`com.google.android.gm`（selected by --package (116 FrameTimeline frames, 0 queueBuffer calls)）
- 裝置 build：`google/oriole/oriole:13/TP1A.220624.014/8819323:userdebug/dev-keys`，Android SDK 33
- 分析區間：19.6 s

> 這是**初判**，不是結論：數字由程式從 trace 萃取，判讀由 LLM 與規則各自提出，每個被引用的數字都經過程式核對（grounding）。請以 Perfetto UI 驗證後再下結論。

## 判讀結果

| 判讀者 | 瓶頸類別 | 信心 | 引用數字核對 |
|---|---|---|---|
| LLM | 未執行（--no-llm） | — | — |
| 規則 baseline | **資料不足** (`insufficient_data`) | high | 1/1 |

### 判斷依據（規則 baseline）

- Only 116 presented frames (< 300).  
  `frames.presented_frame_count` = 116 ✅

- 下一步：Record a longer gameplay session (30-60 s).

## 指標摘要

### Frame pacing

來源：`frame_timeline`（TX - com.google.android.gm/com.google.android.gm.ConversationListActivityGmail#319；present 時間：`surfaceflinger_display_frame`）

| 指標 | 值 |
|---|---|
| frame 數 | 116 |
| 平均 FPS | 39.2 |
| 1% low FPS | 1.9 |
| frame interval P50 / P90 / P99 / max (ms) | 11.12 / 22.27 / 379.79 / 655.61 |
| 慢 frame（≥1.5× 中位數）比例 | 16% |
| jank 比例（app / SF / dropped） | 10%（12 / 0 / 0） |
| jank 類型 | Buffer Stuffing: 34, App Deadline Missed: 12 |
| 前 / 中 / 後段 FPS | 37.1 / 79.5 / 20.9 |
| 閒置空檔（>1 s，已排除） | 1 |

### 關鍵 thread

| thread | 角色 | CPU 使用率 | 等 CPU 比例 | D 狀態 (ms) | cluster 分佈 |
|---|---|---|---|---|---|
| ogle.android.gm | main | 17% | 15% | 73 | cpu7 44%, cpu6 36%, cpu3 5%, cpu5 4%, cpu4 4%, cpu2 3%, cpu0 2%, cpu1 2% |
| RenderThread | render | 2% | 16% | 16 | cpu5 25%, cpu6 24%, cpu7 22%, cpu4 14%, cpu2 4%, cpu3 4%, cpu1 4%, cpu0 3% |

### CPU 頻率（DVFS）

此 trace 無此資料：no cpufreq counters (enable power/cpu_frequency)

### GPU

此 trace 無此資料：no GPU frequency: trace lacks power/gpu_frequency and no sidecar data in window

### Thermal

此 trace 無此資料：no thermal data: trace has no thermal counters and no sidecar data in window

## 資料可用性

| 資料來源 | 有無 |
|---|---|
| frame_timeline | 有 |
| sched | 有 |
| cpufreq | 無 |
| gpufreq | 無 |
| thermal | 無 |
| sidecar | 無 |
