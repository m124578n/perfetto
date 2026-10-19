# Recording a game trace

This guide records a Perfetto trace of a mobile game plus a **sidecar** CSV
(GPU clock/utilisation, thermal zones, Android thermal status, CPU frequency
caps) that some devices cannot put into the trace itself.

Tested on a Samsung Galaxy S24 (SM-S9260, Snapdragon 8 Gen 3, Android 15,
user build, on-device Perfetto v46). No root needed.

## 1. One-time setup

1. Install Android platform-tools (`adb`). `gpa record` looks for it on `PATH`,
   under `ANDROID_HOME`, and in `%LOCALAPPDATA%\Android\Sdk\platform-tools`
   (Android Studio's default on Windows), or pass `--adb`.
2. On the phone: Settings → About phone → Software information → tap
   **Build number** 7 times to unlock Developer options.
3. Settings → Developer options → enable **USB debugging**.
4. Samsung only: Settings → Security and privacy → **Auto Blocker** must be off,
   otherwise USB debugging is blocked.
5. Plug in the cable, unlock the phone, accept **Allow USB debugging** (tick
   "Always allow from this computer").
6. `adb devices` should list the phone as `device`.

## 2. Record

Start the game and get to the scene you want to measure, then:

```bash
uv run gpa record traces/mygame_scene1 --duration 30
```

This:

1. pushes `src/gameperf_agent/device/sidecar.sh` to `/data/local/tmp` and starts
   it in the background (it polls every 0.5 s);
2. runs `perfetto` with `configs/game.pbtx` (config passed on stdin);
3. stops the sampler and pulls `traces/mygame_scene1.pftrace` and
   `traces/mygame_scene1.sidecar.csv`, then deletes the files from the phone.

Play normally during the recording. Write down what you did (scene, graphics
settings, how long the phone had been running) — that becomes the `scenario`
field in `eval/labels.yaml`.

Analyse it:

```bash
uv run gpa analyze traces/mygame_scene1.pftrace            # rules + LLM, writes .report.md
uv run gpa analyze traces/mygame_scene1.pftrace --no-llm   # rules only, no API key needed
```

### Without the helper

```bash
adb shell perfetto --txt -c - -o /data/misc/perfetto-traces/t.pftrace < configs/game.pbtx
adb pull /data/misc/perfetto-traces/t.pftrace
```

Git Bash on Windows rewrites `/data/...` into `C:/Program Files/Git/data/...`;
prefix the commands with `MSYS_NO_PATHCONV=1` (the Python helper is not affected).

## 3. What the config records and why

| Data source | What for | Notes |
|---|---|---|
| `android.surfaceflinger.frametimeline` | Frame pacing and jank types | Android 12+. **Unity/GLES games that present via `eglSwapBuffers` produce no app frames here** (verified); frames then come from atrace `queueBuffer`. |
| ftrace `sched/*`, `task/*` | Where/when each thread runs; Running/Runnable/D states | `compact_sched` keeps it small. |
| ftrace `power/cpu_frequency`, `cpu_idle` | DVFS per cluster | |
| ftrace `power/gpu_frequency`, `thermal/*` | GPU clock, kernel thermal events | **Missing or silent on the S24** (`Ftrace event unknown: power/gpu_frequency`); kept for other devices. |
| atrace `gfx`, `view` | `queueBuffer` / `eglSwapBuffers` slices | Frame fallback for games. |
| `linux.process_stats` | Process and thread names | |
| `linux.system_info` | Per-CPU available frequencies | Used to rank clusters (little/mid/big/prime). |

The config deliberately avoids `sys_stats.thermal_period_ms` and
`gpufreq_period_ms`: the S24's on-device Perfetto (v46) rejects them as unknown
fields and refuses the whole config.

## 4. The sidecar

`sidecar.sh` writes `uptime_s,key,value` rows. `/proc/uptime` is
CLOCK_BOOTTIME, the same clock as Perfetto's default trace clock, so rows line up
with trace timestamps. This was verified: sidecar `scaling_cur_freq` samples
match trace `cpufreq` 100% within a 50 ms read window at offset 0, versus
72–85% when shifted by ±1–2 s (each report prints this check).

| Key | Source (S24) | Meaning |
|---|---|---|
| `gpu_clock_mhz` | `/sys/kernel/gpu/gpu_clock` | Current GPU clock |
| `gpu_busy_pct` | `/sys/kernel/gpu/gpu_busy` | GPU utilisation |
| `gpu_cap_mhz` | `/sys/kernel/gpu/gpu_max_clock` | **Dynamic** GPU max limit (dropped to 231 MHz in a game) |
| `gpu_thermal_pwrlevel` | `/sys/class/kgsl/kgsl-3d0/thermal_pwrlevel` | KGSL thermal power level, 0 = no limit |
| `gpu_hw_max_mhz` | max of `gpu_freq_table` | Hardware max (static) |
| `temp_mc:<zone>` | `/sys/class/thermal/thermal_zone*/` (cpuss, gpuss, xo-therm, battery, skin) | milli-°C |
| `max_khz:policyN` | `scaling_max_freq` | CPU frequency cap (thermal **or** vendor game policy) |
| `cur_khz:policyN` | `scaling_cur_freq` | Used only for the time-alignment check |
| `thermal_status` | `dumpsys thermalservice` (every 2 s) | Android thermal status 0–6 |

**Overhead (measured from the trace itself):** the sampler and its children used
~2.4 s CPU over a 30 s recording ≈ 8% of one core, ≈ 1% of the 8-core total.
Half of that is forking `sleep` every 0.5 s. `--no-sidecar` records without it.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `adb devices` shows `offline` | `adb reconnect offline`, else `adb kill-server && adb start-server`. Happened repeatedly on the S24 after idle periods or re-plugging; `gpa record` tries both automatically. |
| Windows shows the phone but no "ADB Interface" | USB debugging is off or Auto Blocker is on. |
| `unauthorized` | Unlock the phone and accept the USB debugging prompt. |
| Device disappears mid-session | Use a rear motherboard USB port and a data cable; front-panel ports and hubs were flaky. |
| Report says no frames for the game | Was atrace `gfx` enabled? Is the game in the foreground? Pass `--package`. |

## 6. Keeping recordings comparable

- Close the game and let the phone cool between recordings unless you are
  deliberately recording a thermal scenario; a game left running on the charger
  heats the device (one trace started with the CPU subsystem already at 66 °C).
- USB charging adds heat and changes power behaviour. Note it in `scenario`.
- On the S24, CPU `scaling_max_freq` sat at 33–40% of hardware max for the whole
  recording in **both** games, including one at 43 °C with thermal status 0, while an
  idle-home-screen recording had no caps. This looks like a vendor game power policy
  (likely Samsung's Game Optimizing Service) rather than thermal mitigation — an
  observation from two games, not a verified cause. Do not read a constant cap as
  thermal throttling.
