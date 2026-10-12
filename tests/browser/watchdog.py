"""Opt-in CI diagnostics: coroutine locations, never task reprs or frame values."""

import asyncio
import math
import os
import sys
from pathlib import PurePath
from types import CoroutineType, GeneratorType

MAX_TASKS = 32
MAX_FRAMES = 16


def watchdog_seconds() -> float | None:
    raw = os.environ.get("CPM_BROWSER_WATCHDOG_SECONDS", "")
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _label(value: str, limit: int) -> str:
    return "".join(char if char.isprintable() else "?" for char in value)[:limit]


def _locations(task: asyncio.Task) -> list[str]:
    # Task.get_stack() shows only the outer suspended coroutine. Follow its
    # await chain to expose close()/shield() below fixture and SDK wrappers.
    current = task.get_coro()
    result = []
    seen = set()
    while len(result) < MAX_FRAMES and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, CoroutineType):
            frame, current = current.cr_frame, current.cr_await
        elif isinstance(current, GeneratorType):
            frame, current = current.gi_frame, current.gi_yieldfrom
        else:
            break
        if frame is not None:
            code = frame.f_code
            filename = _label(PurePath(code.co_filename).name, 96)
            function = _label(code.co_name, 96)
            result.append(f"{filename}:{frame.f_lineno} in {function}")
    return result


async def report_stall(seconds: float) -> None:
    await asyncio.sleep(seconds)
    own = asyncio.current_task()
    tasks = sorted((task for task in asyncio.all_tasks() if task is not own), key=id)
    lines = [f"Browser async watchdog after {seconds:g}s: {len(tasks)} pending tasks"]
    for index, task in enumerate(tasks[:MAX_TASKS], 1):
        lines.append(f"  Task {index}:")
        locations = _locations(task)
        lines.extend(f"    {location}" for location in locations)
        if not locations:
            lines.append("    No Python coroutine frame available")
        if len(locations) == MAX_FRAMES:
            lines.append("    Further frames omitted")
    if len(tasks) > MAX_TASKS:
        lines.append(f"  {len(tasks) - MAX_TASKS} further tasks omitted")
    # Never use repr(task), task names, traceback formatting (source snippets),
    # frame locals or arguments: they can contain credential-bearing inputs.
    print("\n".join(lines), file=sys.stderr, flush=True)
