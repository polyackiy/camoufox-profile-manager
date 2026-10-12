"""Read-only frozen models must remain usable after their build date ages."""

import ast
import hashlib
import os
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from camoufox import fpgen_model as model

SPEC = Path(__file__).resolve().parents[2] / "packaging" / "camoufox-pm.spec"


@pytest.fixture
def collected_model(tmp_path, monkeypatch):
    # Artificial bytes and hashes exercise upstream pin checking without touching
    # its installed model/cache or relying on a build-machine download.
    files = {"network.json.zst": b"synthetic network", "values.dat.zst": b"synthetic values"}
    pin = {
        "sha256": "verified-model-stamp",
        "tag": "synthetic-model",
        "file_sha256": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
        "decompressed_sha256": {},
    }
    monkeypatch.setattr(model, "PIN", pin)
    monkeypatch.setattr(model, "DECOMPRESSED_FILES", ())
    monkeypatch.delenv(model.CUSTOM_MODEL_ENV, raising=False)
    directory = tmp_path / "fpgen" / "data"
    directory.mkdir(parents=True)
    for name, value in files.items():
        (directory / name).write_bytes(value)
        old = time.time() - 36 * 86400
        os.utime(directory / name, (old, old))
    (directory / model.STAMP).write_text(pin["sha256"] + "\n", encoding="utf-8")
    return directory


def finalize(directory):
    # Load just the spec's packaging function; invoking the spec would launch an
    # actual PyInstaller build. Keep the tested function identical to production.
    function = next(
        node
        for node in ast.parse(SPEC.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "finalize_fpgen_model"
    )
    namespace = {
        "os": os,
        "Path": Path,
        "PIN": model.PIN,
        "PINNED_MTIME": model.PINNED_MTIME,
        "STAMP": model.STAMP,
        "is_pinned": model.is_pinned,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SPEC), "exec"), namespace)
    namespace["finalize_fpgen_model"](directory)


def read_only_launch(directory):
    with (
        patch.object(model.os, "utime", side_effect=PermissionError("read-only installation")),
        patch.object(model, "_install", side_effect=AssertionError("Unexpected model download")),
    ):
        model.ensure_fpgen_model(directory)


def test_collection_aging_failure_and_finalization_prevent_runtime_writes(collected_model):
    assert model.is_pinned(collected_model)
    with pytest.raises(model.FpgenModelError):
        read_only_launch(collected_model)
    finalize(collected_model)
    with patch.object(model.os, "utime", side_effect=AssertionError("Unexpected runtime write")):
        model.ensure_fpgen_model(collected_model)
    assert all(
        int((collected_model / name).stat().st_mtime) == model.PINNED_MTIME
        for name in model.PIN["file_sha256"]
    )


@pytest.mark.parametrize("broken", ["missing-file", "corrupt-file", "missing-stamp", "wrong-stamp"])
def test_packaging_refuses_incomplete_or_unverified_model(collected_model, broken):
    if broken == "missing-file":
        (collected_model / "values.dat.zst").unlink()
    elif broken == "corrupt-file":
        (collected_model / "values.dat.zst").write_bytes(b"corrupted")
    elif broken == "missing-stamp":
        (collected_model / model.STAMP).unlink()
    else:
        (collected_model / model.STAMP).write_text("unverified", encoding="utf-8")
    with pytest.raises((RuntimeError, FileNotFoundError)):
        finalize(collected_model)


@pytest.mark.parametrize("transport", ["installer-copytree", "tar-distribution", "artifact-zip"])
def test_packaging_transports_preserve_model_lifetime(collected_model, tmp_path, transport):
    finalize(collected_model)
    installed = tmp_path / "installed"
    if transport == "installer-copytree":
        # Both macOS DMG staging and Linux deb staging use this metadata-preserving
        # copy before OS packaging; neither may use copyfile for model resources.
        shutil.copytree(collected_model, installed)
    else:
        archive_path = tmp_path / "bundle.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(collected_model, arcname="installed")
        if transport == "artifact-zip":
            # Actions uploads opaque installers inside ZIPs. Extracting that outer
            # ZIP can reset the installer's own date but must not change its bytes
            # or the future dates recorded in its inner archive.
            artifact = tmp_path / "artifact.zip"
            with zipfile.ZipFile(artifact, "w") as outer:
                outer.write(archive_path, arcname=archive_path.name)
            with zipfile.ZipFile(artifact) as outer:
                extracted = Path(outer.extract(archive_path.name, tmp_path / "downloaded"))
            assert extracted.read_bytes() == archive_path.read_bytes()
            archive_path = extracted
        with tarfile.open(archive_path) as archive:
            options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            archive.extractall(tmp_path / "extracted", **options)
        installed = tmp_path / "extracted" / "installed"
    read_only_launch(installed)
    assert (installed / model.STAMP).read_text(encoding="utf-8").strip() == model.PIN["sha256"]
