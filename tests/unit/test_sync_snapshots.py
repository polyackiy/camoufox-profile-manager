"""Adversarial portable archive tests; no real browser or profile secrets."""

import json
import os
import stat
import struct
import zipfile

import pytest

from camoufox_pm.core.models import Profile, ProxyConfig, ProxyType
from camoufox_pm.core.update_permissions import create_private_directory, create_private_file
from camoufox_pm.sync import snapshots as s


@pytest.fixture
def area(tmp_path):
    root = tmp_path.resolve() / "private"
    create_private_directory(root)
    data = root / "browser"
    create_private_directory(data)
    (data / "cookies.sqlite").write_bytes(b"opaque browser cookies")
    return root, data


@pytest.fixture
def compat():
    return s.Compatibility(browser_build="152.0.2-beta.1", os="linux", architecture="x86_64")


def rewrite(path, transform):
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    entries = transform(entries)
    path.unlink()
    with (
        os.fdopen(create_private_file(path), "wb") as output,
        zipfile.ZipFile(output, "w") as archive,
    ):
        for info, data in entries:
            archive.writestr(info, data)


def metadata_change(entries, change):
    result = []
    for info, data in entries:
        if info.filename == "snapshot.json":
            obj = json.loads(data)
            change(obj)
            data = json.dumps(obj).encode()
        result.append((info, data))
    return result


def sample(area, compat):
    root, data = area
    archive = root / "snapshot.zip"
    s.build_snapshot(Profile(name="Portable"), data, archive, compat)
    return archive, root / "stage"


def test_round_trip_preserves_identity_and_only_portable_metadata(area, compat):
    root, data = area
    profile = Profile(
        name="Warm account",
        group="local-group",
        extensions=[],
        notes="Private note",
        fingerprint={"navigator.hardwareConcurrency": 8},
        proxy=ProxyConfig(
            type=ProxyType.SOCKS5,
            server="proxy.invalid:1080",
            username="alice",
            password="private-password",
        ),
    )
    archive = root / "snapshot.zip"
    built = s.build_snapshot(profile, data, archive, compat)
    decoded = s.validate_snapshot(archive, root / "stage", compat)
    assert built == decoded
    assert (root / "stage/data/cookies.sqlite").read_bytes() == b"opaque browser cookies"
    assert decoded.profile.proxy["password"] == "private-password"
    assert set(decoded.profile.apply_fields()) == {
        "name",
        "tags",
        "notes",
        "browser_settings",
        "proxy",
        "fingerprint",
    }
    assert "local-group" not in json.dumps(decoded.model_dump(mode="json"))
    if os.name != "nt":
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        assert stat.S_IMODE((root / "stage/data/cookies.sqlite").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "field,value", [("browser_build", "151-old"), ("os", "windows"), ("architecture", "aarch64")]
)
def test_exact_compatibility_no_downgrade(area, compat, field, value):
    archive, target = sample(area, compat)
    expected = s.Compatibility.model_validate({**compat.model_dump(), field: value})
    with pytest.raises(s.SnapshotError, match="incompatible"):
        s.validate_snapshot(archive, target, expected)
    assert not target.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "a/../escape",
        "a//b",
        "C:/drive",
        "CON.txt",
        "a/AUX",
        "name.",
        "name ",
        "a\\b",
        "a/secret.key",
        ".env",
        "e\u0301",
    ],
)
def test_unsafe_archive_paths_rejected_before_extraction(area, compat, name):
    archive, target = sample(area, compat)
    rewrite(archive, lambda entries: entries + [(zipfile.ZipInfo("data/" + name), b"bad")])
    with pytest.raises(ValueError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


@pytest.mark.parametrize(
    "names", [("A/file", "a/other"), ("Case", "case"), ("a", "a/b"), ("same", "same")]
)
def test_collisions_and_file_directory_conflict(area, compat, names):
    archive, target = sample(area, compat)
    with (
        pytest.warns(UserWarning)
        if names == ("same", "same")
        else __import__("contextlib").nullcontext()
    ):
        rewrite(
            archive,
            lambda entries: entries + [(zipfile.ZipInfo("data/" + name), b"bad") for name in names],
        )
    with pytest.raises(ValueError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFDIR])
def test_archive_special_file_rejected(area, compat, mode):
    archive, target = sample(area, compat)
    info = zipfile.ZipInfo("data/special")
    info.external_attr = (mode | 0o600) << 16
    rewrite(archive, lambda entries: entries + [(info, b"bad")])
    with pytest.raises(s.SnapshotError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


def test_compression_bombs_not_supported(area, compat):
    archive, target = sample(area, compat)

    def compressed(entries):
        entries[0][0].compress_type = zipfile.ZIP_DEFLATED
        return entries

    rewrite(archive, compressed)
    with pytest.raises(s.SnapshotError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "stolen"),
        ("storage_path", "/etc"),
        ("grants", ["admin"]),
        ("schedules", []),
        ("extensions", ["/evil"]),
        ("group", "admin"),
    ],
)
def test_local_authority_fields_rejected(area, compat, field, value):
    archive, target = sample(area, compat)
    rewrite(
        archive,
        lambda entries: metadata_change(entries, lambda obj: obj["profile"].update({field: value})),
    )
    with pytest.raises(ValueError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


def test_unknown_nested_settings_and_fingerprint_path_rejected(area, compat):
    archive, target = sample(area, compat)
    rewrite(
        archive,
        lambda entries: metadata_change(
            entries,
            lambda obj: obj["profile"]["browser_settings"].update({"executable_path": "/evil"}),
        ),
    )
    with pytest.raises(ValueError):
        s.validate_snapshot(archive, target, compat)
    profile = Profile(name="Local", fingerprint={"addons": ["/evil"]})
    with pytest.raises(ValueError):
        s.build_snapshot(profile, area[1], area[0] / "new.zip", compat)


def test_inventory_hash_and_missing_files(area, compat):
    archive, target = sample(area, compat)
    rewrite(
        archive,
        lambda entries: metadata_change(
            entries, lambda obj: obj["files"]["cookies.sqlite"].update({"sha256": "0" * 64})
        ),
    )
    with pytest.raises(s.SnapshotError, match="integrity"):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


def test_existing_target_is_untouched(area, compat):
    archive, target = sample(area, compat)
    create_private_directory(target)
    (target / "valuable").write_text("keep")
    with pytest.raises(s.SnapshotError):
        s.validate_snapshot(archive, target, compat)
    assert (target / "valuable").read_text() == "keep"
    with pytest.raises(s.SnapshotError):
        s.build_snapshot(Profile(name="Local"), area[1], archive, compat)
    assert archive.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("extensions", ["library-id"]),
        ("proxy", ProxyConfig(type=ProxyType.HTTP, server="proxy", password="enc:local-key")),
    ],
)
def test_unportable_source_metadata_fails(area, compat, field, value):
    destination = area[0] / "bad.zip"
    with pytest.raises(ValueError):
        s.build_snapshot(Profile(name="Local", **{field: value}), area[1], destination, compat)
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX special file test")
@pytest.mark.parametrize("kind", ["fifo", "symlink", "hardlink"])
def test_source_special_file_fails_without_blocking(area, compat, kind):
    root, data = area
    if kind == "fifo":
        os.mkfifo(data / "special")
    elif kind == "symlink":
        (data / "special").symlink_to(data / "cookies.sqlite")
    else:
        os.link(data / "cookies.sqlite", data / "special")
    with pytest.raises(s.SnapshotError):
        s.build_snapshot(Profile(name="Local"), data, root / "bad.zip", compat)
    assert not (root / "bad.zip").exists()


def test_bounded_file_count_and_data(area, compat, monkeypatch):
    monkeypatch.setattr(s, "MAX_FILES", 0)
    with pytest.raises(s.SnapshotError, match="many"):
        s.build_snapshot(Profile(name="Local"), area[1], area[0] / "bad.zip", compat)
    monkeypatch.setattr(s, "MAX_FILES", 10)
    monkeypatch.setattr(s, "MAX_BYTES", 2)
    with pytest.raises(s.SnapshotError, match="byte"):
        s.build_snapshot(Profile(name="Local"), area[1], area[0] / "bad.zip", compat)
    assert not (area[0] / "bad.zip").exists()


def test_missing_source_fails(area, compat):
    with pytest.raises(s.SnapshotError):
        s.build_snapshot(Profile(name="Local"), area[0] / "missing", area[0] / "bad.zip", compat)


def test_duplicate_metadata_fields(area, compat):
    archive, target = sample(area, compat)

    def duplicate(entries):
        return [
            (info, data.replace(b'"format_version":1', b'"format_version":1,"format_version":1'))
            if info.filename == "snapshot.json"
            else (info, data)
            for info, data in entries
        ]

    rewrite(archive, duplicate)
    with pytest.raises(s.SnapshotError, match="Duplicate"):
        s.validate_snapshot(archive, target, compat)


def test_truncated_archive(area, compat):
    archive, target = sample(area, compat)
    archive.write_bytes(archive.read_bytes()[:-10])
    with pytest.raises(ValueError):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


@pytest.mark.parametrize("field", ["format_version", "protocol", "schema_version"])
def test_boolean_and_future_version_rejected(area, compat, field):
    archive, target = sample(area, compat)

    def mutate(obj):
        if field == "format_version":
            obj[field] = True
        else:
            obj["compatibility"][field] = True

    rewrite(archive, lambda entries: metadata_change(entries, mutate))
    with pytest.raises(s.SnapshotError):
        s.validate_snapshot(archive, target, compat)


def test_metadata_validation_errors_redact_secrets(area, compat):
    archive, target = sample(area, compat)
    secret = "NEVER-LOG-THIS-CREDENTIAL"
    rewrite(
        archive,
        lambda entries: metadata_change(
            entries, lambda obj: obj["profile"].update({"proxy": {"bad-field": secret}})
        ),
    )
    with pytest.raises(s.SnapshotError) as error:
        s.validate_snapshot(archive, target, compat)
    assert secret not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_at", [0, 1, 2])
async def test_capture_requires_closed_proof_under_lease(area, compat, monkeypatch, reject_at):
    from contextlib import asynccontextmanager

    root, data = area
    held = False
    checks = 0

    @asynccontextmanager
    async def lease(manager, profile_id):
        nonlocal held
        held = True
        try:
            yield Profile(name="Closed")
        finally:
            held = False

    def proof(path):
        nonlocal checks
        assert held
        assert path == data
        checks += 1
        if reject_at == checks:
            raise s.SnapshotError("Browser process remains active")

    monkeypatch.setattr(s, "profile_operation", lease)
    monkeypatch.setattr(s, "profile_data_path", lambda manager, profile: data)
    destination = root / "captured.zip"
    if reject_at:
        with pytest.raises(s.SnapshotError):
            await s.capture_snapshot(None, "local", destination, compat, proof)
        assert not destination.exists()
    else:
        await s.capture_snapshot(None, "local", destination, compat, proof)
        assert destination.exists()
        assert checks == 2
    assert not held


def test_directory_added_during_capture_invalidates_snapshot(area, compat, monkeypatch):
    root, data = area
    original = s._regular_open

    def changing(path):
        result = original(path)
        (data / "new-cookie").write_bytes(b"new")
        return result

    monkeypatch.setattr(s, "_regular_open", changing)
    with pytest.raises(s.SnapshotError, match="changed"):
        s.build_snapshot(Profile(name="Closed"), data, root / "bad.zip", compat)
    assert not (root / "bad.zip").exists()


def test_zip64_cannot_override_bounded_ordinary_directory(area, compat, monkeypatch):
    archive, target = sample(area, compat)
    original = archive.read_bytes()
    end = len(original) - 22
    _, _, _, count, _, length, start, _ = struct.unpack("<4s4H2IH", original[end:])
    zip64_end = struct.pack(
        "<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, length, start
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
    new_end = end + len(zip64_end) + len(locator)
    # Ordinary EOCD claims zero members/bytes, while ZIP64 directs ZipFile to the
    # actual directory. A larger variant would bypass the allocation preflight.
    ordinary_end = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 0, 0, 0, new_end, 0)
    archive.write_bytes(original[:end] + zip64_end + locator + ordinary_end)
    monkeypatch.setattr(s, "MAX_DIRECTORY", 1)

    def forbidden_parser(*args, **kwargs):
        pytest.fail("ZIP64 must fail before ZipFile allocates its directory")

    monkeypatch.setattr(s.zipfile, "ZipFile", forbidden_parser)
    with pytest.raises(s.SnapshotError, match="ZIP64"):
        s.validate_snapshot(archive, target, compat)
    assert not target.exists()


def test_descriptor_recheck_does_not_compare_path_timestamp_precision(area, compat, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    root, data = area
    original = Path.stat
    target = data / "cookies.sqlite"

    def different_precision(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == target:
            values = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
            values["st_mtime_ns"] += 37
            values["st_ctime_ns"] += 53
            return SimpleNamespace(**values)
        return result

    monkeypatch.setattr(Path, "stat", different_precision)
    s.build_snapshot(Profile(name="Closed"), data, root / "snapshot.zip", compat)


def test_reopened_descriptor_still_rejects_post_capture_change(area, compat, monkeypatch):
    root, data = area
    original = s._regular_open
    count = 0

    def changed(path):
        nonlocal count
        count += 1
        if count == 2:
            path.write_bytes(b"changed after capture")
        return original(path)

    monkeypatch.setattr(s, "_regular_open", changed)
    with pytest.raises(s.SnapshotError, match="changed"):
        s.build_snapshot(Profile(name="Closed"), data, root / "bad.zip", compat)
    assert not (root / "bad.zip").exists()


def test_local_sync_marker_excluded_but_nested_names_preserved(area, compat):
    root, data = area
    (data / ".camoufox-pm-sync.json").write_text("local authority")
    (data / "nested").mkdir()
    (data / "nested" / ".camoufox-pm-sync.json").write_text("ordinary nested file")
    archive, stage = sample(area, compat)
    with zipfile.ZipFile(archive) as source:
        assert "data/.camoufox-pm-sync.json" not in source.namelist()
        assert "data/nested/.camoufox-pm-sync.json" in source.namelist()


@pytest.mark.parametrize("name", [".camoufox-pm-sync.json", "data/.camoufox-pm-sync.json"])
def test_archive_cannot_supply_local_sync_marker(area, compat, name):
    archive, stage = sample(area, compat)
    rewrite(archive, lambda entries: entries + [(zipfile.ZipInfo(name), b"copied authority")])
    with pytest.raises(s.SnapshotError, match="local synchronization marker"):
        s.validate_snapshot(archive, stage, compat)


@pytest.mark.skipif(os.name == "nt", reason="Linux legacy marker fixture")
def test_closed_snapshot_excludes_reserved_linux_marker_without_following(
    area, compat, monkeypatch
):
    from camoufox_pm.sync import quiescence as q

    root, data = area
    marker = data / "lock"
    marker.symlink_to(f"127.0.0.1:+{os.getpid()}")
    before = marker.lstat()
    monkeypatch.setattr(q.sys, "platform", "linux")
    monkeypatch.setattr(q.psutil, "process_iter", lambda: iter(()))
    archive = root / "snapshot.zip"
    with q.browser_profile_lock(data):
        s.build_snapshot(Profile(name="Closed"), data, archive, compat)
    with zipfile.ZipFile(archive) as snapshot:
        assert "data/lock" not in snapshot.namelist()
        assert "data/.parentlock" not in snapshot.namelist()
        assert snapshot.read("data/cookies.sqlite") == b"opaque browser cookies"
    assert marker.lstat().st_ino == before.st_ino
    assert os.readlink(marker) == f"127.0.0.1:+{os.getpid()}"


@pytest.mark.parametrize("stable", [False, True])
def test_snapshot_preserves_explicit_canvas_privacy_choice(area, compat, stable):
    from camoufox_pm.core.models import BrowserSettings

    root, data = area
    profile = Profile(name="Canvas choice", browser_settings=BrowserSettings(stable_canvas=stable))
    archive = root / "snapshot.zip"
    s.build_snapshot(profile, data, archive, compat)
    decoded = s.validate_snapshot(archive, root / "stage", compat)
    restored = Profile(**decoded.profile.apply_fields())
    assert restored.browser_settings.stable_canvas is stable
    assert restored.browser_settings == profile.browser_settings
    prefs = restored.to_camoufox_launch_options().get("firefox_user_prefs", {})
    assert (prefs.get("privacy.baselineFingerprintingProtection") is False) is stable


@pytest.mark.parametrize("lifecycle", ["active", "trashed"])
def test_lifecycle_snapshots_require_explicit_format_two(area, compat, lifecycle):
    root, data = area
    archive = root / "lifecycle.zip"
    metadata = s.build_snapshot(Profile(name="Kept"), data, archive, compat, lifecycle=lifecycle)
    assert metadata.format_version == 2 and metadata.lifecycle == lifecycle
    restored = s.validate_snapshot(archive, root / "stage", compat)
    assert restored == metadata
    with pytest.raises(ValueError):
        s.SnapshotMetadata.model_validate({**metadata.model_dump(), "format_version": 1})
