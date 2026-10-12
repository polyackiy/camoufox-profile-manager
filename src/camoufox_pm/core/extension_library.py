"""Validated local WebExtensions and immutable, portable profile snapshots."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from .archive_paths import portable_component
from .database import StaleWriteError
from .leases import make_lease_holder
from .models import generate_short_id
from .profile_operations import profile_data_path, profile_operation, run_file_operation

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_FILES = 2048
MAX_PROFILE_ADDONS = 16
MAX_PROFILE_BYTES = 128 * 1024 * 1024
ADDON_PREFIX = ".cpm-addons"
REF_PATTERN = re.compile(r"^\.cpm-addons/[0-9a-f]{64}$")
ID_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9._-]*@[a-zA-Z0-9._-]+|\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\})$"
)


def initialize_tables(connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS extension_library (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
        addon_id TEXT NOT NULL, permissions TEXT NOT NULL, manifest_version INTEGER NOT NULL,
        sha256 TEXT NOT NULL UNIQUE, unpacked_bytes INTEGER NOT NULL, xpi BLOB NOT NULL,
        row_version INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")


def validate_xpi(payload: bytes) -> dict:
    """Validate all members before extraction; do not interpret executable JS."""
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("Extension upload must be at most 16 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_FILES:
                raise ValueError("Extension contains too many files")
            seen = set()
            total = 0
            for item in entries:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if (
                    not item.filename
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or "\x00" in item.filename
                    or any(ord(char) < 32 for char in item.filename)
                    or item.filename != path.as_posix() + ("/" if item.is_dir() else "")
                    or any(not portable_component(part) for part in path.parts)
                    or len(path.parts) > 20
                    or len(item.filename) > 512
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                    or item.flag_bits & 1
                ):
                    raise ValueError("Extension contains an unsafe archive path or file")
                key = unicodedata.normalize("NFC", item.filename.rstrip("/")).casefold()
                if key in seen:
                    raise ValueError("Extension contains duplicate archive paths")
                seen.add(key)
                total += item.file_size
                if total > MAX_UNPACKED_BYTES or item.file_size > MAX_UNPACKED_BYTES:
                    raise ValueError("Extension exceeds the 64 MB unpacked limit")
                if item.file_size > max(item.compress_size * 200, 1024 * 1024):
                    raise ValueError("Extension compression ratio exceeds the limit")
            if "manifest.json" not in archive.namelist():
                raise ValueError("Extension needs a root manifest.json")
            if archive.getinfo("manifest.json").file_size > 256 * 1024:
                raise ValueError("Extension manifest is too large")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                not isinstance(manifest, dict)
                or type(manifest.get("manifest_version")) is not int
                or manifest.get("manifest_version") not in (2, 3)
            ):
                raise ValueError("Extension requires a Firefox Manifest V2 or V3")
            name, version = manifest.get("name"), manifest.get("version")
            if isinstance(name, str) and re.fullmatch(r"__MSG_[A-Za-z0-9_@]+__", name):
                locale = manifest.get("default_locale")
                if not isinstance(locale, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", locale):
                    raise ValueError("Localized extension names need a valid default locale")
                locale_path = f"_locales/{locale}/messages.json"
                if archive.getinfo(locale_path).file_size > 256 * 1024:
                    raise ValueError("Extension locale metadata is too large")
                messages = json.loads(archive.read(locale_path))
                key = name[6:-2]
                entry = messages.get(key) if isinstance(messages, dict) else None
                name = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 255:
                raise ValueError("Extension needs a name of 1–255 characters")
            if not isinstance(version, str) or not re.fullmatch(
                r"(0|[1-9][0-9]{0,8})(?:\.(0|[1-9][0-9]{0,8})){0,3}", version
            ):
                raise ValueError("Extension needs a numeric version with up to four components")
            settings = manifest.get("browser_specific_settings", manifest.get("applications", {}))
            addon_id = settings.get("gecko", {}).get("id") if isinstance(settings, dict) else None
            if (
                not isinstance(addon_id, str)
                or len(addon_id) > 80
                or not ID_PATTERN.fullmatch(addon_id)
            ):
                raise ValueError("Extension needs an explicit Firefox add-on ID")
            permissions = []
            for key in (
                "permissions",
                "optional_permissions",
                "host_permissions",
                "optional_host_permissions",
            ):
                values = manifest.get(key, [])
                if (
                    not isinstance(values, list)
                    or len(values) > 256
                    or any(not isinstance(value, str) or len(value) > 2048 for value in values)
                ):
                    raise ValueError("Extension permissions must be a bounded list of strings")
                permissions.extend(values)
            scripts = manifest.get("content_scripts", [])
            if not isinstance(scripts, list) or len(scripts) > 256:
                raise ValueError("Extension content scripts must be a bounded list")
            for script in scripts:
                if not isinstance(script, dict) or not isinstance(script.get("matches", []), list):
                    raise ValueError("Invalid extension content-script permissions")
                permissions.extend(script.get("matches", []))
            if len(permissions) > 512 or any(
                not isinstance(value, str) or len(value) > 2048 for value in permissions
            ):
                raise ValueError("Extension requests too many permissions")
            files = {
                unicodedata.normalize("NFC", item.filename).casefold()
                for item in entries
                if not item.is_dir()
            }
            for item in entries:
                if any(
                    unicodedata.normalize(
                        "NFC", PurePosixPath(item.filename).parents[index].as_posix()
                    ).casefold()
                    in files
                    for index in range(len(PurePosixPath(item.filename).parents) - 1)
                ):
                    raise ValueError("Extension archive has conflicting file and directory paths")
            # Read every member to verify CRC before storing any bytes or writes.
            for item in entries:
                if not item.is_dir():
                    with archive.open(item) as source:
                        remaining = item.file_size
                        while chunk := source.read(min(65536, remaining + 1)):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ValueError("Extension archive size mismatch")
            return {
                "name": name.strip(),
                "version": version,
                "addon_id": addon_id,
                "manifest_version": manifest["manifest_version"],
                "permissions": sorted(set(permissions)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "unpacked_bytes": total,
            }
    except (
        zipfile.BadZipFile,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
    ):
        raise ValueError("Invalid Firefox extension archive") from None


def _extract(payload: bytes, target: Path) -> None:
    validate_xpi(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for item in archive.infolist():
            path = target.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, path.open("xb") as destination:
                    shutil.copyfileobj(source, destination, 65536)


def profile_extension_payloads(profile, profile_dir: Path) -> list[bytes]:
    """Read only owned relative paths, including profiles imported from archives."""
    refs = profile.extensions
    if len(refs) > MAX_PROFILE_ADDONS or len(set(refs)) != len(refs):
        raise ValueError("Profile has too many or duplicate extensions")
    root = profile_dir.resolve()
    result = []
    total = 0
    addon_ids = set()
    for ref in refs:
        if not REF_PATTERN.fullmatch(ref):
            raise ValueError("Profile extension paths must be managed relative snapshots")
        directory = root / ref
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not directory.resolve().is_relative_to(root)
        ):
            raise ValueError("A profile extension snapshot is missing or unsafe")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            paths = list(directory.rglob("*"))
            if len(paths) > MAX_FILES:
                raise ValueError("Profile extension contains too many files")
            for path in paths:
                if path.is_symlink():
                    raise ValueError("Profile extension contains a symbolic link")
                if path.is_file():
                    total += path.stat().st_size
                    if total > MAX_PROFILE_BYTES:
                        raise ValueError("Profile extensions exceed 128 MB")
                    archive.write(path, path.relative_to(directory).as_posix())
        payload = buffer.getvalue()
        metadata = validate_xpi(payload)
        if metadata["addon_id"] in addon_ids:
            raise ValueError("Select only one version of each extension")
        addon_ids.add(metadata["addon_id"])
        result.append(payload)
    return result


def resolve_profile_addons(profile, profile_dir: Path) -> list[str]:
    """Parent launch integration: validate imported snapshots before executing JS."""
    profile_extension_payloads(profile, profile_dir)
    return [str((profile_dir / ref).resolve()) for ref in profile.extensions]


def managed_default_exclusions(addon_paths: list[str]):
    """An explicitly assigned uBlock version replaces Camoufox's bundled one."""
    from camoufox.addons import DefaultAddons

    for directory in addon_paths:
        manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
        settings = manifest.get("browser_specific_settings", manifest.get("applications", {}))
        if settings.get("gecko", {}).get("id") == "uBlock0@raymondhill.net":
            return [DefaultAddons.UBO]
    return []


def _content_digest(payload: bytes) -> str:
    """Identify validated file contents independently of ZIP timestamps/compression."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for entry in sorted(archive.infolist(), key=lambda item: item.filename):
            if entry.is_dir():
                continue
            name = entry.filename.encode("utf-8")
            digest.update(len(name).to_bytes(4, "big"))
            digest.update(name)
            digest.update(entry.file_size.to_bytes(8, "big"))
            with archive.open(entry) as member:
                while chunk := member.read(65536):
                    digest.update(chunk)
    return digest.hexdigest()


def _install_snapshots(payloads: list[bytes], profile_dir: Path) -> tuple[list[str], list[Path]]:
    if len(payloads) > MAX_PROFILE_ADDONS:
        raise ValueError("Select at most 16 extensions")
    metadata = [validate_xpi(payload) for payload in payloads]
    if len({item["addon_id"] for item in metadata}) != len(metadata):
        raise ValueError("Select only one version of each extension")
    if sum(item["unpacked_bytes"] for item in metadata) > MAX_PROFILE_BYTES:
        raise ValueError("Profile extensions exceed 128 MB")
    parent = profile_dir / ADDON_PREFIX
    if parent.is_symlink():
        raise ValueError("Profile extension directory cannot be a symbolic link")
    parent.mkdir(parents=True, exist_ok=True)
    created = []
    refs = []
    try:
        for payload, item in zip(payloads, metadata, strict=True):
            ref = f"{ADDON_PREFIX}/{item['sha256']}"
            target = profile_dir / ref
            if target.is_symlink():
                raise ValueError("Profile extension snapshot cannot be a symbolic link")
            if target.exists():
                existing = profile_extension_payloads(
                    SimpleNamespace(extensions=[ref]), profile_dir
                )[0]
                if _content_digest(existing) != _content_digest(payload):
                    raise ValueError(
                        "Existing extension snapshot does not match selected extension"
                    )
            else:
                temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=parent))
                try:
                    _extract(payload, temporary)
                    temporary.rename(target)
                    created.append(target)
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
            refs.append(ref)
        return refs, created
    except BaseException:
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
        raise


def _prune_snapshots(profile_dir: Path, previous: list[str], selected: list[str]) -> None:
    root = profile_dir.resolve()
    for ref in set(previous) - set(selected):
        if not REF_PATTERN.fullmatch(ref):
            continue
        target = profile_dir / ref
        if target.is_symlink():
            target.unlink(missing_ok=True)
        elif target.resolve().is_relative_to(root):
            shutil.rmtree(target, ignore_errors=True)


async def drain_operation(task):
    """Retrieve worker results even after repeated cancellation, for rollback."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    return task.result(), cancelled


class ExtensionLibrary:
    def __init__(self, manager):
        self.manager = manager
        self.connection = manager.storage.db._connection

    def _row(self, extension_id):
        row = self.connection.execute(
            "SELECT * FROM extension_library WHERE id=?", (extension_id,)
        ).fetchone()
        if row is None:
            raise LookupError("Extension not found")
        return row

    @staticmethod
    def _public(row):
        return {
            key: (json.loads(row[key]) if key == "permissions" else row[key])
            for key in (
                "id",
                "name",
                "version",
                "addon_id",
                "permissions",
                "manifest_version",
                "sha256",
                "unpacked_bytes",
                "row_version",
                "created_at",
            )
        }

    async def list(self):
        return [
            self._public(row)
            for row in self.connection.execute(
                "SELECT id,name,version,addon_id,permissions,manifest_version,sha256,unpacked_bytes,row_version,created_at FROM extension_library ORDER BY created_at DESC"
            )
        ]

    async def upload(self, payload: bytes):
        metadata = await run_file_operation(validate_xpi, payload)
        old = self.connection.execute(
            "SELECT * FROM extension_library WHERE sha256=?", (metadata["sha256"],)
        ).fetchone()
        if old:
            return self._public(old)
        extension_id = generate_short_id()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO extension_library(id,name,version,addon_id,permissions,manifest_version,sha256,unpacked_bytes,xpi,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    extension_id,
                    metadata["name"],
                    metadata["version"],
                    metadata["addon_id"],
                    json.dumps(metadata["permissions"]),
                    metadata["manifest_version"],
                    metadata["sha256"],
                    metadata["unpacked_bytes"],
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        row = self.connection.execute(
            "SELECT * FROM extension_library WHERE sha256=?", (metadata["sha256"],)
        ).fetchone()
        return self._public(row)

    async def delete(self, extension_id, row_version):
        self._row(extension_id)
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM extension_library WHERE id=? AND row_version=?",
                (extension_id, row_version),
            )
            if not cursor.rowcount:
                raise StaleWriteError(extension_id, row_version)

    def payloads(self, ids: builtins.list[str]) -> builtins.list[bytes]:
        if len(ids) > MAX_PROFILE_ADDONS or len(ids) != len(set(ids)):
            raise ValueError("Select at most 16 distinct extensions")
        return [bytes(self._row(extension_id)["xpi"]) for extension_id in ids]

    async def profile_extensions(self, profile_id):
        profile = await self.manager.get_profile(profile_id)
        if profile is None:
            raise LookupError("Profile not found")
        payloads = await run_file_operation(
            profile_extension_payloads, profile, profile_data_path(self.manager, profile)
        )
        extensions = []
        for ref, payload in zip(profile.extensions, payloads, strict=True):
            metadata = validate_xpi(payload)
            claimed_sha = ref.rsplit("/", 1)[-1]
            row = self.connection.execute(
                "SELECT xpi FROM extension_library WHERE sha256=?", (claimed_sha,)
            ).fetchone()
            if row is not None:
                matches = await run_file_operation(
                    lambda actual, expected: _content_digest(actual) == _content_digest(expected),
                    payload,
                    bytes(row["xpi"]),
                )
                if matches:
                    metadata["sha256"] = claimed_sha
            extensions.append({**metadata, "id": ref})
        return {"row_version": profile.row_version, "extensions": extensions}

    async def assign(self, profile_id, extension_ids, row_version, retained_refs=None):
        return await self.assign_payloads(
            profile_id, self.payloads(extension_ids), row_version, retained_refs=retained_refs
        )

    async def assign_payloads(self, profile_id, payloads, row_version, retained_refs=None):
        created = []
        committed = False
        holder = make_lease_holder()
        async with profile_operation(self.manager, profile_id, holder=holder) as profile:
            if profile.row_version != row_version:
                raise StaleWriteError(profile_id, row_version)
            if retained_refs:
                if len(retained_refs) != len(set(retained_refs)) or any(
                    ref not in profile.extensions for ref in retained_refs
                ):
                    raise ValueError("Only existing profile extension snapshots may be retained")
                old_payloads = await run_file_operation(
                    profile_extension_payloads, profile, profile_data_path(self.manager, profile)
                )
                payloads = payloads + [
                    payload
                    for ref, payload in zip(profile.extensions, old_payloads, strict=True)
                    if ref in retained_refs
                ]

            try:
                task = asyncio.create_task(
                    asyncio.to_thread(
                        _install_snapshots, payloads, profile_data_path(self.manager, profile)
                    )
                )
                (refs, created), cancelled = await drain_operation(task)
                if cancelled:
                    raise asyncio.CancelledError
                with self.connection:
                    cursor = self.connection.execute(
                        "UPDATE profiles SET extensions=?,row_version=row_version+1,updated_at=? WHERE id=? AND row_version=? AND deleted_at IS NULL AND locked_by=?",
                        (
                            json.dumps(refs),
                            datetime.now().isoformat(),
                            profile_id,
                            row_version,
                            holder,
                        ),
                    )
                    if not cursor.rowcount:
                        raise StaleWriteError(profile_id, row_version)
                committed = True
                await run_file_operation(
                    _prune_snapshots,
                    profile_data_path(self.manager, profile),
                    profile.extensions,
                    refs,
                )
            finally:
                if not committed:
                    for path in created:
                        shutil.rmtree(path, ignore_errors=True)
        return await self.profile_extensions(profile_id)
