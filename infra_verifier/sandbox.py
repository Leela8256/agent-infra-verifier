"""Per-process write guard; no root, global settings, or container daemon changes."""

import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def backend():
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "macos-seatbelt"
    if sys.platform == "linux" and platform.machine() in {"x86_64", "aarch64"}:
        return "linux-landlock"
    raise ValueError(
        "No supported native write guard; use a supported macOS/Linux host"
    )


# Framework runtime lock directories that cannot be relocated by configuration.
# Allowed for every framework, never only for one owner. OpenClaw's exec hard-codes
# /tmp/openclaw-state-locks-<uid> for its SQLite lifecycle locks on macOS/Linux.
RUNTIME_LOCK_DIRS = ("openclaw-state-locks-{uid}",)


def runtime_lock_dirs():
    """Absolute, existing lock directories the guard permits besides output."""
    uid = os.getuid() if hasattr(os, "getuid") else ""
    root = Path("/tmp").resolve()
    out = []
    for name in RUNTIME_LOCK_DIRS:
        path = root / name.format(uid=uid)
        path.mkdir(mode=0o700, exist_ok=True)  # Landlock needs an existing path
        out.append(str(path))
    return out


def command(argv, output):
    mode = backend()
    output = str(Path(output).resolve(strict=True))
    if mode == "macos-seatbelt":
        extra = "".join(" (subpath " + json.dumps(p) + ")" for p in runtime_lock_dirs())
        profile = (
            "(version 1)(allow default)(deny file-write*)(allow file-write* (subpath "
            + json.dumps(output)
            + ')' + extra + ' (literal "/dev/null"))'
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    return [sys.executable, "-m", "infra_verifier.sandbox", output, *argv]


def denied_writes(pids, start, end):
    """v2: write attempts the guard blocked for these processes, or None if unknowable.

    macOS Seatbelt logs every denial to the unified log ("Sandbox: name(pid) deny(1)
    file-write-create /path"), so the host can count them after the run. Linux Landlock
    has no equivalent log, so the result there is None (unscored), never 0.
    """
    import re
    from datetime import timedelta

    if backend() != "macos-seatbelt" or not shutil.which("log"):
        return None
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        result = subprocess.run(
            [
                "log",
                "show",
                "--style",
                "compact",
                "--start",
                (start - timedelta(seconds=1)).strftime(fmt),
                "--end",
                (end + timedelta(seconds=2)).strftime(fmt),
                "--predicate",
                'eventMessage CONTAINS "deny(1) file-write"',
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    found = []
    pattern = re.compile(r"Sandbox: (.+?)\((\d+)\) deny\(1\) (file-write[\w-]*) (.+)$")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match and int(match.group(2)) in pids:
            found.append(
                {
                    "process": match.group(1),
                    "pid": int(match.group(2)),
                    "operation": match.group(3),
                    "path": match.group(4).strip(),
                }
            )
    return found


def landlock(output):
    """ABI >=3 is required: old kernels cannot protect path-based truncation."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)  # landlock_create_ruleset VERSION
    if abi < 3:
        raise OSError("Landlock ABI >=3 is required; refusing an unguarded launch")
    # WRITE_FILE, REMOVE_*, MAKE_*, REFER, TRUNCATE. Read/execute remain available.
    rights = (1 << 1) | sum(1 << bit for bit in range(4, 15))

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    attrs = Ruleset(rights)
    ruleset = libc.syscall(444, ctypes.byref(attrs), ctypes.sizeof(attrs), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    try:
        allowed_paths = [(output, rights), ("/dev/null", (1 << 1))]
        allowed_paths += [(p, rights) for p in runtime_lock_dirs()]
        for path, allowed in allowed_paths:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathBeneath(allowed, fd)
                if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError(ctypes.get_errno(), "landlock_add_rule")
            finally:
                os.close(fd)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
        if libc.syscall(446, ruleset, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(ruleset)


def probe(output):
    """Check real denial and permitted writes before launching the agent."""
    output = Path(output).resolve()
    inside, outside = output / ".guard-probe", output.parent / ".guard-probe"
    code = (
        "import pathlib,sys; inside=pathlib.Path(sys.argv[1]); outside=pathlib.Path(sys.argv[2]); "
        "inside.write_text('probe'); inside.unlink();\n"
        "try: outside.write_text('probe')\n"
        "except PermissionError: sys.exit(0)\n"
        "sys.exit(9)\n"
    )
    result = subprocess.run(
        command([sys.executable, "-c", code, str(inside), str(outside)], output),
        capture_output=True,
        timeout=10,
    )
    for path in (inside, outside):
        path.unlink(missing_ok=True)
    if result.returncode:
        raise ValueError("Native write guard probe failed; no agent was launched")
    return {
        "backend": backend(),
        "probe": "passed",
        "write_root": str(output),
        "runtime_lock_dirs": runtime_lock_dirs(),
        "limitations": "Allows inherited stdout/stderr, /dev/null and the shared framework runtime lock directories listed in runtime_lock_dirs. Read and network access are not restricted. Linux Landlock does not restrict chmod/chown/timestamps/xattrs; use a read-only container for hostile workloads or metadata containment.",
    }


if __name__ == "__main__":
    try:
        landlock(sys.argv[1])
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    except (OSError, ValueError) as error:
        print(f"write guard: {error}", file=sys.stderr)
        raise SystemExit(125)
