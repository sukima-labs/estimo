"""Minimal sd_notify, so systemd knows we are ready and alive (Type=notify, WatchdogSec)."""

from __future__ import annotations

import os
import socket


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr)
        s.sendall(state.encode())
    return True


def watchdog_interval_s() -> float | None:
    usec = os.environ.get("WATCHDOG_USEC")
    return int(usec) / 1_000_000 / 3 if usec else None
