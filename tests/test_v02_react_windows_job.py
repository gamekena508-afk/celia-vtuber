from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_worker_job_handle_terminates_synthetic_child_when_parent_is_terminated(tmp_path: Path):
    ready_path = tmp_path / "child-ready.txt"
    pid_path = tmp_path / "child-pid.txt"
    child_code = (
        "import pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text('ready', encoding='utf-8'); "
        "time.sleep(120)"
    )
    parent_code = f"""
import pathlib, subprocess, sys, time
from bots.react.windows_job import attach_current_process_to_kill_on_close_job

# This reference must stay alive through the worker lifetime.
job_lifetime = attach_current_process_to_kill_on_close_job()
child = subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(ready_path)!r}])
deadline = time.monotonic() + 5
while not pathlib.Path({str(ready_path)!r}).exists():
    if child.poll() is not None or time.monotonic() >= deadline:
        raise RuntimeError('sentinel child did not become ready')
    time.sleep(0.01)
pathlib.Path({str(pid_path)!r}).write_text(str(child.pid), encoding='ascii')
time.sleep(120)
"""
    project_root = Path(__file__).resolve().parents[1]
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        cwd=project_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid: int | None = None

    def process_exited(pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no process with this PID remains
                return True
            raise ctypes.WinError(error)
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0  # WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)

    def terminate_synthetic_child(pid: int) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)

    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not pid_path.exists():
            if parent.poll() is not None:
                stderr = parent.stderr.read() if parent.stderr else ""
                raise AssertionError(f"synthetic worker exited before ready: {stderr}")
            time.sleep(0.02)
        assert ready_path.read_text(encoding="utf-8") == "ready"
        child_pid = int(pid_path.read_text(encoding="ascii"))

        parent.terminate()
        parent.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not process_exited(child_pid):
            time.sleep(0.02)
        assert process_exited(child_pid), "the Job Object left its synthetic child alive"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if child_pid is not None and not process_exited(child_pid):
            terminate_synthetic_child(child_pid)
