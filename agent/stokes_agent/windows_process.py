"""Native Windows process trees, confined before their first instruction executes.

A suspended Popen is assigned to a kill-on-close Job Object before its thread is
resumed. Closing the agent therefore kills descendants even on an unhandled exit.
The process-local registry owns kernel handles, never reconstructs them from saved
PIDs, and lets the existing supervisor cancel safely after an MPI leader exits.
Standard library only; Win32 bindings are loaded only on Windows.
"""

from __future__ import annotations

import ctypes
import subprocess
import threading
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from typing import IO, Any


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("min_working", ctypes.c_size_t),
        ("max_working", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread", wintypes.DWORD),
        ("process", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("delta", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


def _windows_ctypes(name: str) -> Any:
    """Resolve Windows-only ctypes symbols lazily; POSIX type stubs omit them."""
    return getattr(ctypes, name)


def _error() -> OSError:
    code = int(_windows_ctypes("get_last_error")())
    return OSError(code, str(_windows_ctypes("FormatError")(code)))


@lru_cache(maxsize=1)
def _api() -> Any:
    """The dynamic Win32 ABI is the single deliberately untyped boundary."""
    api = _windows_ctypes("WinDLL")("kernel32", use_last_error=True)
    definitions = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "GetProcessTimes": (
            [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
        "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
    }
    for name, (args, result) in definitions.items():
        method = getattr(api, name)
        method.argtypes = args
        method.restype = result
    return api


# Kernel resource ownership must outlive the launch call and be visible to cancel.
_jobs: dict[int, int] = {}
_lock = threading.RLock()


def owns(pid: int) -> bool:
    """Only this agent's registered handles authorize cancellation."""
    with _lock:
        return pid in _jobs


def terminate(pid: int) -> None:
    with _lock:
        job = _jobs.get(pid)
        if job is not None and not _api().TerminateJobObject(job, 1):
            raise _error()


def release(pid: int) -> None:
    """Close after each step; kill-on-close also removes surviving descendants."""
    with _lock:
        job = _jobs.pop(pid, None)
        if job is not None:
            _api().CloseHandle(job)


def process_start(pid: int) -> int | None:
    api = _api()
    handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return None
    try:
        if api.WaitForSingleObject(handle, 0) != 258:
            return None
        times = [ctypes.c_uint64() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        return int(times[0].value)
    finally:
        api.CloseHandle(handle)


def _resume(pid: int) -> None:
    api = _api()
    snapshot = api.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise _error()
    try:
        entry = _ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        found = api.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.process == pid:
                thread = api.OpenThread(0x0002, False, entry.thread)
                if not thread:
                    raise _error()
                try:
                    if api.ResumeThread(thread) == 0xFFFFFFFF:
                        raise _error()
                    return
                finally:
                    api.CloseHandle(thread)
            found = api.Thread32Next(snapshot, ctypes.byref(entry))
        raise OSError("the suspended process has no primary thread")
    finally:
        api.CloseHandle(snapshot)


def launch(
    argv: list[str], cwd: Path, env: dict[str, str], output: IO[bytes]
) -> subprocess.Popen[bytes]:
    """Launch hidden, attach the suspended child to a Job Object, then allow execution."""
    api = _api()
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise _error()
    process: subprocess.Popen[bytes] | None = None
    try:
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise _error()
        process = subprocess.Popen(  # noqa: S603 - validated absolute argv, no shell
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 | 0x00000004,
        )
        handle = api.OpenProcess(0x0001 | 0x0100, False, process.pid)
        if not handle:
            raise _error()
        try:
            if not api.AssignProcessToJobObject(job, handle):
                raise _error()
        finally:
            api.CloseHandle(handle)
        with _lock:
            _jobs[process.pid] = int(job)
        _resume(process.pid)
        return process
    except BaseException:
        if process is not None:
            with _lock:
                _jobs.pop(process.pid, None)
            process.kill()
            process.wait(timeout=5)
        api.CloseHandle(job)
        raise


# One kill-on-close job for children that must not outlive THIS process (the WSL agent's
# `wsl.exe`). Its handle is never closed: Windows closes it when the process ends, however it
# ends, and the kernel then kills every process in the job.
_host_job: dict[str, int] = {}


def confine(pid: int) -> None:
    """Make ``pid`` die with this process. **A graceful `close` does not run on a crash or a
    Task Manager kill**, and the desktop host left one orphaned `wsl.exe` (and its Linux agent)
    behind for every such exit: 14 were found running on the owner's machine."""
    api = _api()
    with _lock:
        if "job" not in _host_job:
            job = api.CreateJobObjectW(None, None)
            if not job:
                raise _error()
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                api.CloseHandle(job)
                raise _error()
            _host_job["job"] = int(job)
        handle = api.OpenProcess(0x0001 | 0x0100, False, pid)
        if not handle:
            raise _error()
        try:
            if not api.AssignProcessToJobObject(_host_job["job"], handle):
                raise _error()
        finally:
            api.CloseHandle(handle)
