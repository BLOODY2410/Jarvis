"""Shared Windows runtime state and local control requests for JARVIS."""

from __future__ import annotations

import ctypes
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

RUNTIME_MUTEX = "Local\\JARVIS_v2_runtime"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102


@dataclass(frozen=True, slots=True)
class RuntimeState:
    pid: int
    root: str
    started_at: float


def state_dir() -> Path:
    base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    directory = base / "JARVIS-v2"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def state_path() -> Path:
    return state_dir() / "runtime.json"


def process_is_running(pid: int) -> bool:
    """Return whether a PID is alive without sending it a Windows console signal."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def read_runtime_state_without_cleanup() -> RuntimeState | None:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return RuntimeState(pid=int(data["pid"]), root=str(data["root"]), started_at=float(data["started_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def clear_runtime_state(pid: int | None = None) -> None:
    target = state_path()
    if pid is not None:
        current = read_runtime_state_without_cleanup()
        if current is not None and current.pid != pid:
            return
    target.unlink(missing_ok=True)


def read_runtime_state() -> RuntimeState | None:
    state = read_runtime_state_without_cleanup()
    if state is None:
        return None
    if process_is_running(state.pid):
        return state
    clear_runtime_state(state.pid)
    return None


def write_runtime_state(pid: int, root: Path) -> RuntimeState:
    state = RuntimeState(pid=pid, root=str(root.resolve()), started_at=time.time())
    target = state_path()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(state)), encoding="utf-8")
    temporary.replace(target)
    return state


def any_runtime_is_running() -> bool:
    if read_runtime_state() is not None:
        return True
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenMutexW(_SYNCHRONIZE, False, RUNTIME_MUTEX)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _request_path(name: str) -> Path:
    return state_dir() / name


def request_activation(pid: int) -> None:
    _request_path("activate.request").write_text(str(pid), encoding="ascii")


def request_stop(pid: int) -> None:
    _request_path("stop.request").write_text(str(pid), encoding="ascii")


def consume_request(name: str, pid: int) -> bool:
    path = _request_path(name)
    try:
        requested_pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    if requested_pid != pid:
        return False
    path.unlink(missing_ok=True)
    return True
