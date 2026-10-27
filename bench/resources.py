"""Observe only owned Rust children on Linux/WSL, including kernel lifetime peaks."""

from pathlib import Path
import threading
import time

from bench.stats import check

RSS_FIELDS = ["monitor_id", "case_id", "pid", "monotonic_ns", "rss_kib", "hwm_kib"]


def parse_status(text):
    fields = {}
    for line in text.splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            parts = line.split()
            check(len(parts) == 3 and parts[2] == "kB" and parts[1].isdigit(), "invalid proc memory counter")
            check(parts[0] not in fields, "duplicate proc memory counter")
            fields[parts[0]] = int(parts[1])
    check(set(fields) == {"VmRSS:", "VmHWM:"} and fields["VmHWM:"] >= fields["VmRSS:"], "missing or inconsistent RSS/HWM")
    return fields["VmRSS:"], fields["VmHWM:"]


class MemoryMonitor:
    """Start after spawn, sample through case completion, stop before terminating its child."""

    def __init__(self, process, case_id, monitor_id, interval):
        self.process, self.case_id, self.monitor_id, self.interval = process, case_id, monitor_id, interval
        self.started_ns = None
        self.finished_ns = None
        self.rows = []
        self.error = None
        self._stop = threading.Event()
        self._thread = None

    def sample(self):
        if self.process.poll() is not None:
            self.error = "owned process exited before monitoring completed"
            return
        try:
            rss, hwm = parse_status(Path(f"/proc/{self.process.pid}/status").read_text())
            self.rows.append({"monitor_id": self.monitor_id, "case_id": self.case_id, "pid": self.process.pid,
                              "monotonic_ns": time.perf_counter_ns(), "rss_kib": rss, "hwm_kib": hwm})
        except Exception:
            self.error = "cannot read owned process memory counters"

    def start(self):
        check(self.started_ns is None, "RSS monitor already started")
        self.started_ns = time.perf_counter_ns()
        self.sample()
        check(self.error is None and self.rows, "RSS unavailable; use Linux/WSL with procfs")

        def loop():
            while not self._stop.wait(self.interval):
                self.sample()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def close(self):
        if self.finished_ns is not None:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            check(not self._thread.is_alive(), "RSS sampling thread did not stop")
        self.sample()
        self.finished_ns = time.perf_counter_ns()
        check(self.error is None and self.rows, "RSS observation failed")

    def record(self):
        check(self.started_ns is not None and self.finished_ns is not None and self.error is None, "incomplete RSS monitor")
        check(self.process.poll() is not None, "RSS workload left an owned Rust child running")
        return {"monitor_id":self.monitor_id,"case_id":self.case_id,"pid":self.process.pid,
                "started_ns":self.started_ns,"finished_ns":self.finished_ns,"sample_count":len(self.rows),
                "peak_rss_kib":max(row["hwm_kib"] for row in self.rows),"server_stopped":True}


class MemoryRegistry:
    def __init__(self, interval=0.005):
        check(type(interval) in (int, float) and 0.001 <= interval <= 1, "RSS interval must be 0.001..1 seconds")
        self.interval = interval
        self.monitors = []

    def __call__(self, process, case_id):
        monitor = MemoryMonitor(process, case_id, f"{case_id}-{len(self.monitors) + 1:03}", self.interval)
        self.monitors.append(monitor)
        return monitor
