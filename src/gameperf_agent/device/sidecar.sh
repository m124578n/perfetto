#!/system/bin/sh
# Sidecar sampler: polls sysfs/dumpsys values that Perfetto cannot record on
# some user builds (e.g. GPU clock and thermal on a Galaxy S24).
#
# Usage: sh sidecar.sh <out.csv> <run_flag_file> [period_s=0.5] [status_every_n=4]
# Runs until <run_flag_file> is deleted. Output is long-format CSV:
#   uptime_s,key,value
# uptime_s is /proc/uptime (CLOCK_BOOTTIME), the same clock as Perfetto's
# default trace clock, so rows can be aligned with trace timestamps.

OUT="$1"
FLAG="$2"
PERIOD="${3:-0.5}"
STATUS_EVERY="${4:-4}"

first() { for f in "$@"; do [ -r "$f" ] && { echo "$f"; return; }; done; }
# Emits "<uptime>,<key>,<first word of file>" using the read builtin (no fork per value).
emit() { read -r v _ < "$2" && echo "$up,$1,$v"; }

GPU_CLK=$(first /sys/kernel/gpu/gpu_clock)
GPU_BUSY=$(first /sys/kernel/gpu/gpu_busy /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage)
# gpu_max_clock is a *dynamic* cap (seen dropping to 231 MHz in a game);
# the hardware max is the first entry of the frequency table.
GPU_CAP=$(first /sys/kernel/gpu/gpu_max_clock)
GPU_TABLE=$(first /sys/kernel/gpu/gpu_freq_table /sys/class/kgsl/kgsl-3d0/freq_table_mhz)
GPU_THERMAL=$(first /sys/class/kgsl/kgsl-3d0/thermal_pwrlevel)

ZONES=""
for z in /sys/class/thermal/thermal_zone*; do
  read -r t < "$z/type" 2>/dev/null || continue
  case "$t" in
    cpuss-*|gpuss-*|xo-therm|battery|*skin*|sys-therm-*) ZONES="$ZONES $z" ;;
  esac
done

POLICIES=$(ls -d /sys/devices/system/cpu/cpufreq/policy* 2>/dev/null)

read up _ < /proc/uptime
{
  echo "uptime_s,key,value"
  if [ -n "$GPU_TABLE" ]; then
    m=0
    for f in $(cat "$GPU_TABLE"); do [ "$f" -gt "$m" ] && m=$f; done
    echo "$up,gpu_hw_max_mhz,$m"
  fi
  for p in $POLICIES; do
    emit "hw_max_khz:${p##*/}" "$p/cpuinfo_max_freq"
  done
} > "$OUT"

i=0
while [ -f "$FLAG" ]; do
  read up _ < /proc/uptime
  {
    [ -n "$GPU_CLK" ] && emit gpu_clock_mhz "$GPU_CLK"
    [ -n "$GPU_BUSY" ] && emit gpu_busy_pct "$GPU_BUSY"
    [ -n "$GPU_CAP" ] && emit gpu_cap_mhz "$GPU_CAP"
    [ -n "$GPU_THERMAL" ] && emit gpu_thermal_pwrlevel "$GPU_THERMAL"
    for z in $ZONES; do
      read -r t < "$z/type"
      emit "temp_mc:$t" "$z/temp"
    done
    for p in $POLICIES; do
      emit "max_khz:${p##*/}" "$p/scaling_max_freq"
      emit "cur_khz:${p##*/}" "$p/scaling_cur_freq"
    done
    if [ $((i % STATUS_EVERY)) -eq 0 ]; then
      s=$(dumpsys thermalservice 2>/dev/null | grep -m1 "Thermal Status")
      echo "$up,thermal_status,${s##*: }"
    fi
  } >> "$OUT" 2>/dev/null
  i=$((i + 1))
  sleep "$PERIOD"
done
