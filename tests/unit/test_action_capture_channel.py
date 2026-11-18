"""Native IPC acceptance against a synthetic launcher/browser/host tree."""

import asyncio
import json
import os
import stat
import struct
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import pytest

from camoufox_pm.core import action_capture_channel
from camoufox_pm.core.action_capture import CaptureRejected, decode_message, encode_message

ROOT = """import subprocess, sys
with subprocess.Popen([sys.executable, "-c", sys.argv[1], "-profile", *sys.argv[2:]]) as child:
    raise SystemExit(child.wait())
"""
BROWSER = """import subprocess, sys
with subprocess.Popen([sys.executable, "-c", sys.argv[-2], sys.argv[-1]]) as child:
    raise SystemExit(child.wait())
"""
NATIVE_HOST = """from camoufox_pm.core.action_capture_host import main
raise SystemExit(main())
"""
FORGED_HELLO = """import json, os
from multiprocessing.connection import Client
from camoufox_pm.core.action_capture import decode_message, encode_message, message_proof
config = json.loads(os.environ.pop("CPM_ACTION_CAPTURE_PROBE"))
with Client(config["address"], config["family"]) as peer:
    challenge = decode_message(peer.recv_bytes(4096))["challenge"]
    identity = {"kind": "host", "pid": CLAIMED_PID, "generation": config["generation"]}
    proof = SUPPLIED_PROOF
    peer.send_bytes(encode_message({"value": identity, "proof": proof}))
    try:
        peer.recv_bytes(4096)
    except (OSError, EOFError):
        raise SystemExit(0)
raise SystemExit(1)
"""
PARTIAL_FRAME = """import json, os
from multiprocessing.connection import Client
config = json.loads(os.environ.pop("CPM_ACTION_CAPTURE_PROBE"))
with Client(config["address"], config["family"]) as peer:
    peer.recv_bytes(4096)
    os.write(peer.fileno(), b"\\x00\\x00")
    try:
        peer.recv_bytes(4096)
    except (OSError, EOFError):
        raise SystemExit(0)
raise SystemExit(1)
"""
ADDON = "synthetic@camoufox-pm.local"
ORIGIN = "https://example.invalid"
# Pin the image reported by the OS, just as the manager does for Camoufox.
# The venv's executable can be a redirector or a framework launcher, with
# a different on-disk path and an extra process in the ancestry.
EXECUTABLE = Path(psutil.Process().exe())


@asynccontextmanager
async def native_host(channel, profile, *, script=NATIVE_HOST):
    env = dict(
        os.environ,
        CPM_ACTION_CAPTURE_PROBE=channel.environment(ADDON, host_executable=EXECUTABLE),
        PYTHONPATH=os.pathsep.join(sys.path),
    )
    process = await asyncio.create_subprocess_exec(
        str(EXECUTABLE),
        "-c",
        ROOT,
        BROWSER,
        str(profile),
        script,
        ADDON,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        yield process
    finally:
        channel.stop()
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 8)


async def send(process, value):
    frame = encode_message(value)
    process.stdin.write(struct.pack("=I", len(frame)) + frame)
    await process.stdin.drain()


async def response(process):
    (length,) = struct.unpack("=I", await asyncio.wait_for(process.stdout.readexactly(4), 5))
    assert 0 < length <= 4096
    return decode_message(await asyncio.wait_for(process.stdout.readexactly(length), 5))


def scoped_message(channel, kind, **fields):
    return {
        "kind": kind,
        "tab": 7,
        "generation": channel.generation,
        "origin": ORIGIN,
        "width": 900,
        "height": 700,
        **fields,
    }


@pytest.mark.asyncio
async def test_native_host_can_only_send_events_after_exact_arm(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    async with native_host(channel, tmp_path / "profile") as process:
        await send(process, scoped_message(channel, "ready"))
        binding = await channel.await_ready(psutil.Process(process.pid))
        assert binding == action_capture_channel.CaptureBinding(7, ORIGIN, 900, 700)
        await channel.arm(binding)
        assert await response(process) == {"arm": True}
        wheel = scoped_message(
            channel, "scroll", sequence=1, x=3, y=4, delta_x=0, delta_y=25, delta_mode=0
        )
        await send(process, wheel)

        def discard_wheel(value, proof):
            assert channel.accept(value, proof) == wheel
            return False

        assert await channel.read_event(discard_wheel) == wheel
        assert await response(process) == {"accepted": True}
        click = scoped_message(channel, "click", sequence=2, x=3, y=4)
        await send(process, click)
        assert await channel.read_event() == click
        assert await response(process) == {"accepted": True}
        await send(process, click)
        with pytest.raises(CaptureRejected):
            await channel.read_event()
        assert await asyncio.wait_for(process.stdout.read(), 5) == b""


@pytest.mark.asyncio
async def test_wrong_profile_does_not_authenticate_with_correct_key(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    async with native_host(channel, tmp_path / "different") as process:
        await send(process, scoped_message(channel, "ready"))
        with pytest.raises(CaptureRejected):
            await channel.await_ready(psutil.Process(process.pid))
        assert await asyncio.wait_for(process.stdout.read(), 5) == b""


@pytest.mark.asyncio
async def test_pinned_browser_process_can_be_the_launch_root(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    async with native_host(channel, tmp_path / "profile") as process:
        await send(process, scoped_message(channel, "ready"))

        async def browser_process():
            driver = psutil.Process(process.pid)
            while not (children := driver.children()):
                await asyncio.sleep(0.01)
            return children[0]

        assert await channel.await_ready(
            await asyncio.wait_for(browser_process(), 5)
        ) == action_capture_channel.CaptureBinding(7, ORIGIN, 900, 700)


@pytest.mark.asyncio
async def test_wrong_binding_revokes_the_single_connection(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    async with native_host(channel, tmp_path / "profile") as process:
        await send(process, scoped_message(channel, "ready"))
        binding = await channel.await_ready(psutil.Process(process.pid))
        with pytest.raises(CaptureRejected):
            await channel.arm(action_capture_channel.CaptureBinding(8, ORIGIN, 900, 700))
        with pytest.raises(CaptureRejected):
            await channel.arm(binding)
        assert await asyncio.wait_for(process.stdout.read(), 5) == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pid", "proof"),
    [
        pytest.param(
            "os.getppid()",
            'message_proof(bytes.fromhex(config["key"]), challenge, identity)',
            id="signed-forged-pid",
        ),
        pytest.param("os.getpid()", "'á'", id="malformed-proof"),
    ],
)
async def test_wrong_native_peer_credentials_fail_closed(tmp_path, pid, proof):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    script = FORGED_HELLO.replace("CLAIMED_PID", pid).replace("SUPPLIED_PROOF", proof)
    async with native_host(channel, tmp_path / "profile", script=script) as process:
        with pytest.raises(CaptureRejected):
            await channel.await_ready(psutil.Process(process.pid))
        assert await asyncio.wait_for(process.wait(), 5) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="Unix stream frames can split")
@pytest.mark.asyncio
async def test_partial_unix_frame_cannot_stall_manager_or_survive_expiry(tmp_path):
    channel = action_capture_channel.CaptureChannel(
        tmp_path / "profile", EXECUTABLE, startup_timeout=2
    )
    async with native_host(channel, tmp_path / "profile", script=PARTIAL_FRAME) as process:
        with pytest.raises(CaptureRejected):
            await asyncio.wait_for(channel.await_ready(psutil.Process(process.pid)), 4)
        assert await asyncio.wait_for(process.wait(), 5) == 0


@pytest.mark.asyncio
async def test_expiry_revokes_armed_capture_and_unblocks_waiting_reader(tmp_path):
    channel = action_capture_channel.CaptureChannel(
        tmp_path / "profile", EXECUTABLE, session_timeout=1
    )
    stopped = []

    def stop_capture_owner():
        stopped.append(True)
        channel.stop()

    channel.on_stop(stop_capture_owner)
    async with native_host(channel, tmp_path / "profile") as process:
        await send(process, scoped_message(channel, "ready"))
        await channel.arm(await channel.await_ready(psutil.Process(process.pid)))
        assert await response(process) == {"arm": True}
        with pytest.raises(CaptureRejected):
            await asyncio.wait_for(channel.read_event(), 4)
        with pytest.raises(CaptureRejected):
            await channel.read_event()
    assert stopped == [True]


@pytest.mark.asyncio
async def test_stopping_capture_exits_idle_native_host_without_browser_input(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    async with native_host(channel, tmp_path / "profile") as process:
        await send(process, scoped_message(channel, "ready"))
        await channel.arm(await channel.await_ready(psutil.Process(process.pid)))
        assert await response(process) == {"arm": True}
        assert not process.stdin.is_closing()
        await channel.close()
        assert await asyncio.wait_for(process.wait(), 5) == 1


@pytest.mark.asyncio
async def test_stopped_capture_notifies_owner_during_registration(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    channel.stop()
    stopped = asyncio.Event()
    channel.on_stop(stopped.set)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_windows_pipe_close_waits_for_pending_buffer_cancellation():
    waiting, settled, closed = asyncio.Event(), threading.Event(), threading.Event()
    permission_to_settle = threading.Event()

    class Overlapped:
        event = 42

        def cancel(self):
            pass  # CancelIoEx requests cancellation.

        def GetOverlappedResult(self, wait):
            assert wait
            assert permission_to_settle.wait(5)
            settled.set()
            return 0, 995  # ERROR_OPERATION_ABORTED

    class WinApi:
        WAIT_OBJECT_0 = 0

        def WaitForSingleObject(self, event, delay):
            waiting.set()
            return 258  # WAIT_TIMEOUT

        def CloseHandle(self, handle):
            assert settled.is_set()
            closed.set()

    transport = action_capture_channel._WindowsTransport.__new__(
        action_capture_channel._WindowsTransport
    )
    transport._api = WinApi()
    transport._handle = 123
    transport._operation = None
    transport._retirement = None
    transport._loop = asyncio.get_running_loop()
    task = asyncio.create_task(transport._complete(Overlapped(), pending=True))
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        task.cancel()
        transport.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not closed.is_set()
        permission_to_settle.set()
        await asyncio.wait_for(transport.wait_closed(), 5)
        assert closed.is_set()
    finally:
        permission_to_settle.set()


@pytest.mark.skipif(sys.platform == "win32", reason="Filesystem socket permissions")
@pytest.mark.asyncio
async def test_unix_endpoint_is_private_and_removed_on_stop(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    try:
        config = json.loads(channel.environment(ADDON, host_executable=EXECUTABLE))
        endpoint = Path(config["address"])
        assert endpoint.is_socket()
        assert stat.S_IMODE(endpoint.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600
    finally:
        channel.stop()
    assert not endpoint.parent.exists()


@pytest.mark.asyncio
async def test_dispatch_clock_fence_revokes_before_due_timer_has_run(tmp_path):
    channel = action_capture_channel.CaptureChannel(tmp_path / "profile", EXECUTABLE)
    stopped = asyncio.Event()
    channel.on_stop(stopped.set)
    loop = asyncio.get_running_loop()
    channel._expiry.cancel()
    channel._expiry = loop.call_at(loop.time() - 1, channel.stop)
    try:
        # No event-loop yield since scheduling the due callback. A last
        # authorization check must still revoke rather than dispatch.
        assert not stopped.is_set()
        assert not channel.is_open()
        assert stopped.is_set()
    finally:
        await channel.close()
