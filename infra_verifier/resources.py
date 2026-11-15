"""Portable sampled process-tree accounting. This is not a container security boundary."""

import os
import signal
import sys

import psutil


class ProcessTree:
    def __init__(self, pid):
        self.pid = pid
        self.known = {}
        self.cpu_by_identity = {}
        self.peak_rss = 0
        self.errors = []
        try:
            self._remember(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass

    def _remember(self, process):
        self.known[(process.pid, process.create_time())] = process

    def sample(self):
        for process in list(self.known.values()):
            try:
                for child in process.children(recursive=True):
                    self._remember(child)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self.errors.append("process tree access denied")
        rss = 0
        live = []
        for identity, process in list(self.known.items()):
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                with process.oneshot():
                    times = process.cpu_times()
                    rss += process.memory_info().rss
                    self.cpu_by_identity[identity] = max(
                        self.cpu_by_identity.get(identity, 0), times.user + times.system
                    )
                live.append(process.pid)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                self.errors.append("process resource access denied")
        self.peak_rss = max(self.peak_rss, rss)
        return {
            "rss_mb": rss / 2**20,
            "cpu_time_s": sum(self.cpu_by_identity.values()),
            "processes": len(live),
        }

    def survivors(self):
        result = []
        for process in self.known.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    result.append(process)
            except psutil.NoSuchProcess:
                pass
        return result

    def kill(self):
        # Covers descendants in the original session even between samples.
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Also stop observed children that created their own process group.
        for process in self.survivors():
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        # The supervisor owns wait4(leader); psutil must not reap it first.
        psutil.wait_procs([p for p in self.survivors() if p.pid != self.pid], timeout=2)


def rusage_metrics(usage):
    # Darwin reports ru_maxrss in bytes; Linux reports KiB.
    return {
        "reaped_cpu_time_s": usage.ru_utime + usage.ru_stime,
        "reaped_peak_rss_mb": usage.ru_maxrss
        / (2**20 if sys.platform == "darwin" else 1024),
    }
