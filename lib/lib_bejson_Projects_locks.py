"""
File Name:       lib_bejson_Projects_locks.py
Relative Path:   lib/lib_bejson_Projects_locks.py
Family:          Projects
Description:     Path lock for the Projects family: a process-wide per-path thread lock in front of Core's
                 ResilientPIDLock. Core's PID lock is correct between processes but two threads of one process can
                 race inside its stale-lock reclaim (observed as an intermittent FileNotFoundError while writing
                 lock_meta.json). Core is frozen, so threads are serialised here and the PID lock still guards
                 other processes.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   4f394ad0-dd12-4298-ad37-23737e12379f
"""

import threading
from pathlib import Path
from typing import Dict, Union

from lib_bejson_Core_bejson_core import ResilientPIDLock

VERSION = "1.8.0"
_registry_guard = threading.Lock()
_thread_locks: Dict[str, threading.Lock] = {}


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _registry_guard:
        return _thread_locks.setdefault(key, threading.Lock())


class ProjectsPathLock:
    """Context manager: thread lock for this path, then the cross-process PID lock. acquire() -> bool."""

    def __init__(self, target_path: Union[str, Path], timeout_seconds: int = 10):
        self._thread_lock = _thread_lock_for(Path(target_path))
        self._pid_lock = ResilientPIDLock(target_path, timeout_seconds)
        self._timeout = timeout_seconds
        self._held_thread = False

    def acquire(self) -> bool:
        if not self._thread_lock.acquire(timeout=self._timeout):
            return False
        self._held_thread = True
        if self._pid_lock.acquire():
            return True
        self.release()
        return False

    def release(self) -> None:
        self._pid_lock.release()
        if self._held_thread:
            self._held_thread = False
            self._thread_lock.release()

    def __enter__(self):
        if not self.acquire():
            raise OSError(53, f"could not lock {self._pid_lock.target}")
        return self

    def __exit__(self, *exc):
        self.release()
        return False
