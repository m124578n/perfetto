"""Builds small synthetic Perfetto traces for unit tests.

The proto bindings bundled with the `perfetto` package omit FrameTimelineEvent,
so frame timeline packets are hand-encoded from the field numbers in
protos/perfetto/trace/android/frame_timeline_event.proto (v57.2).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import TracePacket

SF_PID = 500
SF_NAME = "/system/bin/surfaceflinger"
SEQ_ID = 1

# FrameTimelineEvent.JankType bitmask values.
JANK_NONE = 1
JANK_SF_SCHEDULING = 2
JANK_PREDICTION_ERROR = 4
JANK_DISPLAY_HAL = 8
JANK_SF_CPU_DEADLINE_MISSED = 16
JANK_SF_GPU_DEADLINE_MISSED = 32
JANK_APP_DEADLINE_MISSED = 64
JANK_BUFFER_STUFFING = 128
JANK_DROPPED = 1024

# FrameTimelineEvent.PresentType values.
PRESENT_ON_TIME = 1
PRESENT_LATE = 2
PRESENT_DROPPED = 4

# TracePacket.frame_timeline_event field number.
_FRAME_TIMELINE_EVENT = 76


# --- minimal protobuf wire encoding -------------------------------------------------


def _varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _int_field(num: int, value: int) -> bytes:
    return _varint(num << 3) + _varint(int(value))


def _bytes_field(num: int, payload: bytes) -> bytes:
    return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def _msg(fields: dict[int, int | bool | str | bytes]) -> bytes:
    out = b""
    for num, value in fields.items():
        if isinstance(value, str):
            out += _bytes_field(num, value.encode())
        elif isinstance(value, bytes):
            out += _bytes_field(num, value)
        else:
            out += _int_field(num, int(value))
    return out


# --- trace builder --------------------------------------------------------------------


@dataclass
class SynthTrace:
    """Accumulates packets; call `to_bytes()` / `to_file()` when done."""

    _packets: list[bytes] = field(default_factory=list)
    _next_cookie: int = 1
    _next_token: int = 1

    def _packet(self, ts: int, extra: bytes = b"", configure=None) -> None:
        pkt = TracePacket()
        pkt.timestamp = ts
        pkt.trusted_packet_sequence_id = SEQ_ID
        if configure:
            configure(pkt)
        self._packets.append(pkt.SerializeToString() + extra)

    def _ftl(self, ts: int, oneof_field: int, body: bytes) -> None:
        self._packet(ts, _bytes_field(_FRAME_TIMELINE_EVENT, _bytes_field(oneof_field, body)))

    def _cookie(self) -> int:
        self._next_cookie += 1
        return self._next_cookie

    def _token(self) -> int:
        self._next_token += 1
        return self._next_token

    def add_processes(self, procs: dict[int, str], ts: int = 1) -> SynthTrace:
        """procs: pid -> process name. SurfaceFlinger is always added."""
        all_procs = {SF_PID: SF_NAME, **procs}

        def configure(pkt: TracePacket) -> None:
            for pid, name in all_procs.items():
                p = pkt.process_tree.processes.add()
                p.pid = pid
                p.ppid = 1
                p.cmdline.append(name)

        self._packet(ts, configure=configure)
        return self

    def add_threads(self, pid: int, threads: dict[int, str], ts: int = 1) -> SynthTrace:
        """threads: tid -> thread name, all belonging to process `pid`."""

        def configure(pkt: TracePacket) -> None:
            for tid, name in threads.items():
                t = pkt.process_tree.threads.add()
                t.tid = tid
                t.tgid = pid
                t.name = name

        self._packet(ts, configure=configure)
        return self

    def add_cpus(self, clusters: list[tuple[int, int, list[int]]], ts: int = 1) -> SynthTrace:
        """clusters: (cpu_count, capacity or 0 for unknown, available freqs in kHz), in cpu order."""

        def configure(pkt: TracePacket) -> None:
            for count, capacity, freqs in clusters:
                for _ in range(count):
                    cpu = pkt.cpu_info.cpus.add()
                    cpu.processor = "synthetic"
                    cpu.frequencies.extend(freqs)
                    if capacity:
                        cpu.capacity = capacity

        self._packet(ts, configure=configure)
        return self

    def add_runs(self, cpu: int, runs: list[tuple[int, int, int, int]]) -> SynthTrace:
        """Schedules threads on one cpu. runs: (tid, start_ns, end_ns, end_state), non-overlapping.

        end_state uses kernel prev_state encoding: 0 = runnable (preempted),
        1 = sleeping (S), 2 = uninterruptible (D). The cpu is idle between runs.
        """

        def configure(pkt: TracePacket) -> None:
            bundle = pkt.ftrace_events
            bundle.cpu = cpu
            for tid, start, end, end_state in sorted(runs, key=lambda r: r[1]):
                for ts, prev, prev_state, nxt in ((start, 0, 0, tid), (end, tid, end_state, 0)):
                    ev = bundle.event.add()
                    ev.timestamp = ts
                    ev.pid = prev
                    sw = ev.sched_switch
                    sw.prev_pid, sw.prev_state, sw.prev_prio = prev, prev_state, 120
                    sw.next_pid, sw.next_prio = nxt, 120
                    sw.prev_comm = f"t{prev}" if prev else "swapper"
                    sw.next_comm = f"t{nxt}" if nxt else "swapper"

        self._packet(min(r[1] for r in runs), configure=configure)
        return self

    def add_atrace_slices(self, pid: int, tid: int, slices: list[tuple[str, int, int]], cpu: int = 0) -> SynthTrace:
        """Atrace begin/end markers (tracing_mark_write) on thread `tid`. slices: (name, start, end).

        Nesting is expressed by ordering: an inner slice must start after and end before its parent.
        """
        events = []
        for name, start, end in slices:
            events.append((start, 0, f"B|{pid}|{name}\n"))
            events.append((end, 1, "E|{pid}\n".replace("{pid}", str(pid))))

        def configure(pkt: TracePacket) -> None:
            bundle = pkt.ftrace_events
            bundle.cpu = cpu
            # Ends sort before begins at the same timestamp; nested ends follow LIFO order of starts.
            for ts, _, buf in sorted(events, key=lambda e: (e[0], -e[1] if e[1] else 0)):
                ev = bundle.event.add()
                ev.timestamp = ts
                ev.pid = tid
                ev.print.buf = buf

        self._packet(min(s[1] for s in slices), configure=configure)
        return self

    def add_cpu_freq(self, cpu: int, samples: list[tuple[int, int]]) -> SynthTrace:
        """samples: (ts_ns, freq_khz) via the power/cpu_frequency ftrace event."""

        def configure(pkt: TracePacket) -> None:
            bundle = pkt.ftrace_events
            bundle.cpu = cpu
            for ts, khz in samples:
                ev = bundle.event.add()
                ev.timestamp = ts
                ev.pid = 0
                ev.cpu_frequency.cpu_id = cpu
                ev.cpu_frequency.state = khz

        self._packet(samples[0][0], configure=configure)
        return self

    def add_frame(
        self,
        *,
        pid: int,
        layer: str,
        start: int,
        app_end: int,
        present: int,
        jank: int = JANK_NONE,
        present_type: int = PRESENT_ON_TIME,
        on_time_finish: bool = True,
        sf_jank: int | None = None,
        with_sf: bool = True,
    ) -> SynthTrace:
        """One app surface frame plus its SurfaceFlinger display frame.

        The display frame spans [start, present]; its end is the present time.
        """
        display_token = self._token()
        surface_token = self._token()
        sf_jank = jank if sf_jank is None else sf_jank

        # SurfaceFlinger display frame (expected + actual).
        if with_sf:
            c = self._cookie()
            self._ftl(start, 1, _msg({1: c, 2: display_token, 3: SF_PID}))
            self._ftl(present, 5, _msg({1: c}))
            c = self._cookie()
            self._ftl(
                start,
                2,
                _msg({1: c, 2: display_token, 3: SF_PID, 4: present_type, 5: on_time_finish, 6: False, 7: sf_jank, 8: 1}),
            )
            self._ftl(present, 5, _msg({1: c}))

        # App surface frame (expected + actual).
        c = self._cookie()
        self._ftl(start, 3, _msg({1: c, 2: surface_token, 3: display_token, 4: pid, 5: layer}))
        self._ftl(app_end, 5, _msg({1: c}))
        c = self._cookie()
        self._ftl(
            start,
            4,
            _msg(
                {
                    1: c,
                    2: surface_token,
                    3: display_token,
                    4: pid,
                    5: layer,
                    6: present_type,
                    7: on_time_finish,
                    8: False,
                    9: jank,
                    10: 1,
                }
            ),
        )
        self._ftl(app_end, 5, _msg({1: c}))
        return self

    def to_bytes(self) -> bytes:
        return b"".join(_bytes_field(1, p) for p in self._packets)

    def to_file(self) -> io.BytesIO:
        return io.BytesIO(self.to_bytes())


MS = 1_000_000


def steady_frames(
    trace: SynthTrace,
    *,
    pid: int,
    layer: str,
    count: int,
    interval_ms: float,
    t0: int = 1_000 * MS,
    work_ms: float = 4.0,
    latency_ms: float = 20.0,
) -> int:
    """Adds `count` evenly spaced, jank-free frames. Returns the next start time."""
    t = t0
    for _ in range(count):
        trace.add_frame(
            pid=pid,
            layer=layer,
            start=t,
            app_end=t + int(work_ms * MS),
            present=t + int(latency_ms * MS),
        )
        t += int(interval_ms * MS)
    return t
