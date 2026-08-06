#!/usr/bin/env python3
"""Force-close all Camoufox browser processes.

Emergency cleanup for browsers left running after a crash. Run:

    uv run python scripts/kill_browsers.py
"""

import sys
import time

import psutil
from loguru import logger


def _find_camoufox_processes() -> list[psutil.Process]:
    """Return every running process that looks like Camoufox."""
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = " ".join(proc.info["cmdline"] or []).lower()
            if "camoufox" in name or "camoufox" in cmdline:
                processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return processes


def kill_camoufox_processes() -> int:
    """Terminate all Camoufox processes; force-kill any survivors."""
    processes = _find_camoufox_processes()
    if not processes:
        logger.info("No Camoufox processes found")
        return 0

    logger.info("Found %d Camoufox processes" % len(processes))
    killed = 0
    for proc in processes:
        try:
            proc.terminate()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(3)

    for proc in processes:
        try:
            if proc.is_running():
                proc.kill()
        except psutil.NoSuchProcess:
            continue

    logger.info("Terminated %d Camoufox processes" % killed)
    return killed


def main() -> None:
    try:
        killed = kill_camoufox_processes()
        print(f"Terminated {killed} processes" if killed else "No active Camoufox processes found")
    except KeyboardInterrupt:
        print("Interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
