"""One protected native-messaging connection for a reviewed browser launch."""

from __future__ import annotations

import asyncio
import ctypes
import hmac
import json
import math
import os
import secrets
import socket
import struct
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

import psutil

from .action_capture import (
    MAX_MESSAGE_BYTES,
    CaptureGate,
    CaptureRejected,
    decode_message,
    encode_message,
    message_proof,
)
from .update_permissions import _WindowsSecurity, verify_private_path

_RESPONSE_SECONDS = 4.0  # Native host waits at most five seconds for a response.
_REVIEW_SECONDS = 60.0  # Native host waits 65 seconds for the reviewed arm.
_T = TypeVar("_T")


class _Transport(Protocol):
    address: str
    family: str

    async def accept(self) -> int: ...
    async def receive(self) -> bytes: ...
    async def send(self, payload: bytes) -> None: ...
    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...


class _UnixTransport:
    family = "AF_UNIX"

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._peer: socket.socket | None = None
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        try:
            self._directory = Path(tempfile.mkdtemp(prefix="cpm-cap-")).resolve()
        except BaseException:
            self._listener.close()
            raise
        self.address = str(self._directory / "ipc")
        try:
            verify_private_path(self._directory)
            self._listener.setblocking(False)
            self._listener.bind(self.address)
            os.chmod(self.address, 0o600)
            verify_private_path(Path(self.address))
            self._listener.listen(1)
        except BaseException:
            self.close()
            raise

    async def accept(self) -> int:
        peer, _ = await self._loop.sock_accept(self._listener)
        self._peer = peer
        self._listener.close()
        Path(self.address).unlink()
        if sys.platform == "darwin":
            # SOL_LOCAL / LOCAL_PEERPID in Darwin's sys/un.h.
            return int(peer.getsockopt(0, 2))
        if sys.platform.startswith("linux"):
            pid, uid, _ = struct.unpack(
                "=iii", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid == os.geteuid():
                return int(pid)
        raise CaptureRejected()

    async def _read_exactly(self, size: int) -> bytes:
        assert self._peer is not None
        result = bytearray()
        while len(result) < size:
            chunk = await self._loop.sock_recv(self._peer, size - len(result))
            if not chunk:
                raise CaptureRejected()
            result.extend(chunk)
        return bytes(result)

    async def receive(self) -> bytes:
        # multiprocessing.connection uses a signed big-endian length on Unix.
        (length,) = struct.unpack("!i", await self._read_exactly(4))
        if not 0 < length <= MAX_MESSAGE_BYTES:
            raise CaptureRejected()
        return await self._read_exactly(length)

    async def send(self, payload: bytes) -> None:
        assert self._peer is not None
        await self._loop.sock_sendall(self._peer, struct.pack("!i", len(payload)) + payload)

    def close(self) -> None:
        self._listener.close()
        if self._peer is not None:
            self._peer.close()
        with suppress(FileNotFoundError):
            Path(self.address).unlink()
        with suppress(FileNotFoundError):
            self._directory.rmdir()

    async def wait_closed(self) -> None:
        pass  # Socket close is synchronous.


class _WindowsTransport:
    family = "AF_PIPE"

    def __init__(self, generation: str) -> None:
        import _winapi

        self._api: Any = _winapi
        self._loop = asyncio.get_running_loop()
        self._operation: Any = None
        self._retirement: asyncio.Future[None] | None = None
        self.address = rf"\\.\pipe\cpm-capture-{generation}"
        api = _WindowsSecurity()
        wintypes = api.w
        pointer = ctypes.c_void_p
        self._peer_pid = api.kernel.GetNamedPipeClientProcessId
        self._peer_pid.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        self._peer_pid.restype = wintypes.BOOL
        self._pid_type = wintypes.ULONG

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.DWORD),
                ("descriptor", pointer),
                ("inherit", wintypes.BOOL),
            ]

        descriptor = pointer()
        api._check(
            api.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{api.user_sid}D:P(A;;FA;;;{api.user_sid})", 1, ctypes.byref(descriptor), None
            )
        )
        create_pipe = api.kernel.CreateNamedPipeW
        create_pipe.argtypes = [wintypes.LPCWSTR, *([wintypes.DWORD] * 6), pointer]
        create_pipe.restype = wintypes.HANDLE
        try:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            # FILE_FLAG_FIRST_PIPE_INSTANCE / PIPE_REJECT_REMOTE_CLIENTS.
            handle = create_pipe(
                self.address,
                self._api.PIPE_ACCESS_DUPLEX
                | self._api.FILE_FLAG_OVERLAPPED
                | self._api.FILE_FLAG_FIRST_PIPE_INSTANCE,
                self._api.PIPE_TYPE_MESSAGE | self._api.PIPE_READMODE_MESSAGE | 0x00000008,
                1,
                MAX_MESSAGE_BYTES,
                MAX_MESSAGE_BYTES,
                0,
                ctypes.byref(attributes),
            )
            if handle in (None, pointer(-1).value):
                raise CaptureRejected()
            self._handle: int | None = handle
        finally:
            api.kernel.LocalFree(descriptor)

    async def _complete(self, operation: Any, *, pending: bool) -> int:
        self._operation = operation
        try:
            if pending:
                while self._api.WaitForSingleObject(operation.event, 0) != self._api.WAIT_OBJECT_0:
                    await asyncio.sleep(0.01)
                pending = False
            count, error = operation.GetOverlappedResult(False)
            if error:
                raise CaptureRejected()
            return int(count)
        except BaseException:
            if pending:
                handle, self._handle = self._handle, None
                assert handle is not None
                self._retirement = self._loop.run_in_executor(
                    None, self._settle_cancelled, operation, handle
                )
            raise
        finally:
            self._operation = None

    def _settle_cancelled(self, operation: Any, handle: int) -> None:
        # CancelIoEx only requests cancellation. Keep both the buffer and
        # the handle alive until the kernel completes the overlapped request.
        try:
            with suppress(OSError):
                try:
                    operation.cancel()
                finally:
                    operation.GetOverlappedResult(True)
        finally:
            with suppress(OSError):
                self._api.CloseHandle(handle)

    async def accept(self) -> int:
        operation = self._api.ConnectNamedPipe(self._handle, overlapped=True)
        await self._complete(operation, pending=True)
        pid = self._pid_type()
        if not self._peer_pid(self._handle, ctypes.byref(pid)):
            raise CaptureRejected()
        return int(pid.value)

    async def receive(self) -> bytes:
        operation, error = self._api.ReadFile(self._handle, MAX_MESSAGE_BYTES, overlapped=True)
        if error not in (0, self._api.ERROR_IO_PENDING):
            operation.cancel()
            raise CaptureRejected()
        await self._complete(operation, pending=bool(error))
        return bytes(operation.getbuffer())

    async def send(self, payload: bytes) -> None:
        operation, error = self._api.WriteFile(self._handle, payload, overlapped=True)
        if error not in (0, self._api.ERROR_IO_PENDING):
            operation.cancel()
            raise CaptureRejected()
        if await self._complete(operation, pending=bool(error)) != len(payload):
            raise CaptureRejected()

    def close(self) -> None:
        # An active coroutine has just been cancelled by CaptureChannel.stop.
        # Its completion handler will retire the only pipe instance.
        if self._handle is not None and self._operation is None:
            handle, self._handle = self._handle, None
            self._api.CloseHandle(handle)

    async def wait_closed(self) -> None:
        if self._retirement is not None:
            await asyncio.shield(self._retirement)


@dataclass(frozen=True, slots=True)
class CaptureBinding:
    tab: int
    origin: str
    width: int
    height: int

    def validate(self) -> None:
        if not (
            type(self.tab) is int
            and 0 <= self.tab <= 2**31 - 1
            and type(self.width) is int
            and 0 < self.width <= 2**31 - 1
            and type(self.height) is int
            and 0 < self.height <= 2**31 - 1
            and type(self.origin) is str
            and not any(char.isspace() for char in self.origin)
        ):
            raise CaptureRejected()
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or self.origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise CaptureRejected()
        # Reading the port also rejects malformed or out-of-range values.
        _ = parsed.port


class CaptureChannel:
    """An internal launch capability whose connection and gate live on one loop."""

    def __init__(
        self,
        profile: Path,
        executable: Path,
        *,
        startup_timeout: float = 180,
        session_timeout: float = 120,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        if not (
            math.isfinite(startup_timeout)
            and 0 < startup_timeout <= 300
            and math.isfinite(session_timeout)
            and 0 < session_timeout <= 120
        ):
            raise CaptureRejected()
        self._host_executable: Path | None = None
        self.generation = secrets.token_hex(16)
        self._key = secrets.token_bytes(32)
        self._challenge = secrets.token_hex(32)
        self._transport: _Transport
        try:
            self._manager = psutil.Process()
            self._profile = profile.resolve()
            self._browser_executable = executable.resolve(strict=True)
            self._transport = (
                _WindowsTransport(self.generation)
                if sys.platform == "win32"
                else _UnixTransport(self._loop)
            )
        except (OSError, ValueError, RuntimeError, psutil.Error):
            raise CaptureRejected() from None
        self._session_timeout = session_timeout
        self._startup_deadline = self._loop.time() + startup_timeout
        self._expiry = self._loop.call_at(self._startup_deadline, self.stop)
        self._operation: asyncio.Task[Any] | None = None
        self._stopped = False
        self._stop_observed = False
        self._stop_observer: Callable[[], None] | None = None
        self._root: psutil.Process | None = None
        self._host: psutil.Process | None = None
        self._browser: psutil.Process | None = None
        self._binding: CaptureBinding | None = None
        self._gate: CaptureGate | None = None
        self._review_deadline = 0.0
        self._event_deadline = 0.0
        self._pending: tuple[dict, str] | None = None
        self._accepted: dict | None = None

    def _require_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise CaptureRejected()

    @contextmanager
    def _guard(self) -> Iterator[None]:
        self._require_loop()
        try:
            yield
        except Exception:
            self.stop()
            raise CaptureRejected() from None
        except asyncio.CancelledError:
            self.stop()
            raise

    def environment(self, addon_id: str, *, host_executable: Path) -> str:
        """Only pass this JSON as the private native host launch environment."""
        with self._guard():
            if self._stopped or self._host_executable is not None or not addon_id:
                raise CaptureRejected()
            self._host_executable = host_executable.resolve(strict=True)
            return json.dumps(
                {
                    "address": self._transport.address,
                    "family": self._transport.family,
                    "key": self._key.hex(),
                    "generation": self.generation,
                    "addon_id": addon_id,
                }
            )

    async def _io(self, operation: Coroutine[Any, Any, _T], deadline: float) -> _T:
        remaining = deadline - self._loop.time()
        if self._stopped or self._operation is not None or remaining <= 0:
            operation.close()
            raise CaptureRejected()
        task = self._loop.create_task(operation)
        self._operation = task
        try:
            result = await asyncio.wait_for(task, remaining)
            if self._stopped or self._loop.time() >= deadline:
                raise CaptureRejected()
            return result
        except asyncio.CancelledError:
            if self._stopped:
                raise CaptureRejected() from None
            raise
        finally:
            self._operation = None

    async def _send(self, value: dict, deadline: float) -> None:
        await self._io(
            self._transport.send(encode_message(value)),
            min(deadline, self._loop.time() + _RESPONSE_SECONDS),
        )

    async def _receive(self, deadline: float) -> tuple[dict, str]:
        envelope = decode_message(await self._io(self._transport.receive(), deadline))
        if set(envelope) != {"value", "proof"}:
            raise CaptureRejected()
        value, proof = envelope["value"], envelope["proof"]
        if (
            not isinstance(value, dict)
            or not isinstance(proof, str)
            or not proof.isascii()
            or not hmac.compare_digest(proof, message_proof(self._key, self._challenge, value))
        ):
            raise CaptureRejected()
        return value, proof

    def _live_browser(self) -> psutil.Process:
        host, root = self._host, self._root
        if (
            host is None
            or root is None
            or not host.is_running()
            or not root.is_running()
            or Path(host.exe()).resolve() != self._host_executable
        ):
            raise CaptureRejected()
        ancestors = host.parents()
        try:
            root_index = ancestors.index(root)
        except ValueError:
            raise CaptureRejected() from None
        if self._manager not in ancestors[root_index + 1 :]:
            raise CaptureRejected()
        for browser in ancestors[: root_index + 1]:
            if Path(browser.exe()).resolve() != self._browser_executable:
                continue
            if self._browser is not None and browser != self._browser:
                continue
            args = browser.cmdline()
            indices = [
                index
                for index, arg in enumerate(args)
                if arg.lower().partition("=")[0] in {"-p", "-profile", "--profile"}
            ]
            if len(indices) != 1:
                continue
            index = indices[0]
            if (
                args[index] == "-profile"
                and index + 1 < len(args)
                and Path(args[index + 1]).is_absolute()
                and Path(args[index + 1]).resolve() == self._profile
                and browser.is_running()
            ):
                return browser
        raise CaptureRejected()

    async def await_ready(self, launch_process: psutil.Process) -> CaptureBinding:
        """Pin the OS peer, its browser/profile and its exact manager-owned launcher."""
        with self._guard():
            if self._root is not None or self._host_executable is None:
                raise CaptureRejected()
            self._root = launch_process
            pid = await self._io(self._transport.accept(), self._startup_deadline)
            if pid <= 0:
                raise CaptureRejected()
            self._host = psutil.Process(pid)
            self._browser = self._live_browser()
            deadline = min(self._startup_deadline, self._loop.time() + _RESPONSE_SECONDS)
            await self._send({"challenge": self._challenge}, deadline)
            hello, _ = await self._receive(deadline)
            if (
                set(hello) != {"kind", "pid", "generation"}
                or hello["kind"] != "host"
                or type(hello["pid"]) is not int
                or hello["pid"] != pid
                or hello["generation"] != self.generation
            ):
                raise CaptureRejected()
            await self._send({"ok": True}, deadline)
            ready, _ = await self._receive(self._startup_deadline)
            if (
                set(ready) != {"kind", "tab", "generation", "origin", "width", "height"}
                or ready["kind"] != "ready"
                or ready["generation"] != self.generation
            ):
                raise CaptureRejected()
            binding = CaptureBinding(ready["tab"], ready["origin"], ready["width"], ready["height"])
            binding.validate()
            self._live_browser()
            self._binding = binding
            self._review_deadline = self._loop.time() + _REVIEW_SECONDS
            self._expiry.cancel()
            self._expiry = self._loop.call_at(self._review_deadline, self.stop)
            return binding

    async def arm(self, binding: CaptureBinding) -> None:
        """Arm the exact reviewed binding only. A mismatch ends this generation."""
        with self._guard():
            binding.validate()
            if (
                self._stopped
                or self._gate is not None
                or binding != self._binding
                or self._loop.time() >= self._review_deadline
            ):
                raise CaptureRejected()
            self._live_browser()
            self._gate = CaptureGate(
                self._key,
                self.generation,
                self._challenge,
                tab=binding.tab,
                origin=binding.origin,
                width=binding.width,
                height=binding.height,
                expires_at=time.monotonic() + self._session_timeout,
            )
            self._event_deadline = self._loop.time() + self._session_timeout
            self._expiry.cancel()
            self._expiry = self._loop.call_at(self._event_deadline, self.stop)
            await self._send({"arm": True}, self._event_deadline)

    def accept(self, value: dict, proof: str) -> dict:
        """Advance the gate only while submitting this particular received event."""
        self._require_loop()
        if (
            self._stopped
            or self._gate is None
            or self._pending is None
            or self._pending[0] is not value
            or self._pending[1] != proof
        ):
            self.stop()
            raise CaptureRejected()
        self._pending = None
        try:
            self._accepted = self._gate.accept(value, proof)
        except CaptureRejected:
            self.stop()
            raise
        return self._accepted.copy()

    async def read_event(self, submit: Callable[[dict, str], bool] | None = None) -> dict:
        """Read, validate and submit without transferring ownership off the manager loop."""
        with self._guard():
            try:
                if self._stopped or self._gate is None:
                    raise CaptureRejected()
                value, proof = await self._receive(self._event_deadline)
                self._live_browser()
                self._pending = value, proof
                if submit is None:
                    self.accept(value, proof)
                submitted = submit is None or submit(value, proof)
                accepted = self._accepted
                if (
                    self._stopped
                    or accepted is None
                    or (not submitted and accepted["kind"] != "scroll")
                ):
                    raise CaptureRejected()
                await self._send({"accepted": True}, self._event_deadline)
                return accepted
            finally:
                self._pending = None
                self._accepted = None

    def is_open(self) -> bool:
        """Fence dispatch even when the expiry timer is behind another ready task."""
        self._require_loop()
        if not self._stopped and self._loop.time() >= self._expiry.when():
            self.stop()
        return not self._stopped

    def on_stop(self, callback: Callable[[], None]) -> None:
        """Notify the owning run synchronously when its generation is revoked."""
        self._require_loop()
        if self._stop_observed:
            raise CaptureRejected()
        self._stop_observed = True
        if self._stopped:
            callback()
        else:
            self._stop_observer = callback

    def stop(self) -> None:
        """Revoke synchronously before releasing resources or returning to the caller."""
        self._require_loop()
        if self._stopped:
            return
        self._stopped = True
        self._key = b""
        self._pending = None
        self._accepted = None
        if self._gate is not None:
            self._gate.stop()
        self._expiry.cancel()
        if self._operation is not None:
            self._operation.cancel()
        observer, self._stop_observer = self._stop_observer, None
        try:
            if observer is not None:
                observer()
        finally:
            with suppress(OSError):
                self._transport.close()

    async def close(self) -> None:
        operation = self._operation
        self.stop()
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await self._transport.wait_closed()
