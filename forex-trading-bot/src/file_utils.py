"""
File I/O utilities for safe state persistence.

Provides:
1. atomic_save() — write to temp file, then atomic rename (prevents corrupt state on crash)
2. FileLock context manager — prevents concurrent cron instances from stepping on each other
"""

import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def atomic_save(path: str | Path, data: Any, indent: int = 2) -> None:
    """
    Atomically write data to a JSON file.
    
    1. Serialise to a temp file in the same directory (same filesystem → rename is atomic)
    2. Rename temp → target (atomic on POSIX — single filesystem operation)
    
    If the process crashes mid-write, the original file is untouched.
    If a partial temp file is left behind, it won't match the target name so it's harmless.
    
    Args:
        path: Target file path
        data: JSON-serialisable data
        indent: JSON indentation level (default: 2)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temporary file in the *same directory* (guaranteed same filesystem)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, default=str)
            f.flush()
            os.fsync(f.fileno())  # Force flush to disk
        # Atomic rename (POSIX guarantee: rename is atomic on the same filesystem)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: str | Path, record: dict) -> None:
    """
    Append a JSON line to a JSONL file.
    
    Uses atomic_save for read-modify-write of the full file.
    For JSONL, we can't easily append atomically, but we can:
    1. Read existing lines
    2. Append new record
    3. Write whole file atomically
    
    This is O(n) per append but for journal files with ~thousands of rows
    it's fine (each line is ~200 bytes, so 1000 entries = ~200 KB → ~1ms write).
    
    For high-throughput scenarios, consider a log-shipper pattern instead.
    """
    path = Path(path)
    records = []
    if path.exists():
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
                records.append(record)
        except (json.JSONDecodeError, OSError):
            records = [record]
    else:
        records = [record]
    atomic_save(path, records, indent=None)  # Compact JSON for append


class FileLock:
    """
    Cross-process file lock using POSIX flock.
    
    Prevents two cron cycles from running simultaneously.
    
    Usage:
        with FileLock("logs/bot.lock"):
            run_once(...)
    
    If another instance holds the lock, this blocks until acquired
    (or raises TimeoutError if timeout is set).
    """
    
    def __init__(self, lock_path: str | Path, timeout: float = 0):
        """
        Args:
            lock_path: Path to the lock file (e.g. "logs/bot.lock")
            timeout: Max seconds to wait for lock. 0 = immediate (non-blocking).
                     None = block indefinitely.
        """
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._fd: Optional[int] = None
    
    def __enter__(self) -> "FileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        
        mode = fcntl.LOCK_EX  # Exclusive lock
        if self.timeout == 0:
            mode |= fcntl.LOCK_NB  # Non-blocking
        
        try:
            fcntl.flock(self._fd, mode)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise TimeoutError(
                f"Could not acquire lock {self.lock_path} — "
                f"another bot cycle is still running"
            )
        
        # Write PID to lock file for debugging
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.truncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode())
        os.fsync(self._fd)
        
        return self
    
    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            # Release lock (automatically released on fd close, but explicit is cleaner)
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None