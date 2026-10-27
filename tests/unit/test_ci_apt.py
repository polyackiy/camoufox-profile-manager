"""Ubuntu runner provisioning preserves repository trust and mirror metadata."""

import os

import pytest
from scripts import configure_ci_apt as apt


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_list_sources_preserve_options_suites_components_and_comments():
    source = (
        "# deb http://azure.archive.ubuntu.com/ubuntu comment main\n"
        "deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu.gpg] "
        "http://azure.archive.ubuntu.com/ubuntu jammy main restricted\n"
        "deb-src http://security.ubuntu.com/ubuntu/ jammy-security universe # keep\n"
        "deb mirror+file:/etc/apt/apt-mirrors.txt jammy-updates multiverse\n"
        "deb https://packages.example.org/ubuntu jammy main\n"
    )
    expected = (
        "# deb http://azure.archive.ubuntu.com/ubuntu comment main\n"
        "deb [arch=amd64 signed-by=/usr/share/keyrings/ubuntu.gpg] "
        "https://archive.ubuntu.com/ubuntu jammy main restricted\n"
        "deb-src https://security.ubuntu.com/ubuntu/ jammy-security universe # keep\n"
        "deb mirror+file:/etc/apt/apt-mirrors.txt jammy-updates multiverse\n"
        "deb https://packages.example.org/ubuntu jammy main\n"
    )
    assert apt.normalize_sources(source, ".list") == expected


def test_deb822_uri_continuations_preserve_signed_by_and_other_fields():
    source = (
        "Types: deb deb-src\r\n"
        "URIs: http://azure.archive.ubuntu.com/ubuntu\r\n"
        " https://security.ubuntu.com/ubuntu/ https://packages.example.org/ubuntu\r\n"
        "Suites: noble noble-updates noble-security\r\n"
        "Components: main universe restricted multiverse\r\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\r\n"
        "X-Note: http://azure.archive.ubuntu.com/ubuntu\r\n"
        "\r\n"
        "# URIs: http://azure.archive.ubuntu.com/ubuntu\r\n"
        "Types: deb\r\n"
        "URIs: mirror+file:/etc/apt/apt-mirrors.txt\r\n"
    )
    expected = source.replace(
        "URIs: http://azure.archive.ubuntu.com/ubuntu\r\n",
        "URIs: https://archive.ubuntu.com/ubuntu\r\n",
        1,
    )
    assert apt.normalize_sources(source, ".sources") == expected


def test_mirror_file_preserves_priorities_and_filters():
    source = (
        "# mirror list — preserve metadata\n"
        "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1 arch:amd64 type:deb\n"
        "http://security.ubuntu.com/ubuntu\tpriority:2 suite:jammy-security\n"
        "https://packages.example.org/ubuntu\tpriority:3\n"
    )
    expected = (
        "# mirror list — preserve metadata\n"
        "https://archive.ubuntu.com/ubuntu/\tpriority:1 arch:amd64 type:deb\n"
        "https://security.ubuntu.com/ubuntu\tpriority:2 suite:jammy-security\n"
        "https://packages.example.org/ubuntu\tpriority:3\n"
    )
    assert apt.normalize_sources(source, "mirror") == expected


@pytest.mark.parametrize(
    "uri",
    [
        "http://azure.archive.ubuntu.com.evil/ubuntu",
        "http://user:password@azure.archive.ubuntu.com/ubuntu",
        "http://azure.archive.ubuntu.com:8080/ubuntu",
        "http://azure.archive.ubuntu.com/ubuntu-private",
        "http://azure.archive.ubuntu.com/ubuntu?token=private",
        "http://azure.archive.ubuntu.com/ubuntu#private",
        "http://azure.archive.ubuntu.com/ubuntu?",
        "http://ports.ubuntu.com/ubuntu-ports",
        "mirror+file:/etc/apt/apt-mirrors.txt",
        "\x00http://azure.archive.ubuntu.com/ubuntu",
        " http://azure.archive.ubuntu.com/ubuntu",
    ],
)
def test_only_exact_approved_repository_uris_change(uri):
    assert apt.normalize_uri(uri) == uri


def test_configure_handles_runner_mirror_indirection_and_is_idempotent(tmp_path):
    source = put(
        tmp_path,
        "etc/apt/sources.list",
        "deb mirror+file:/etc/apt/apt-mirrors.txt jammy main restricted universe multiverse\n",
    )
    original_source = source.read_bytes()
    mirror = put(
        tmp_path,
        "etc/apt/apt-mirrors.txt",
        "http://azure.archive.ubuntu.com/ubuntu\tpriority:1\n",
    )
    deb822 = put(
        tmp_path,
        "etc/apt/sources.list.d/ubuntu.sources",
        "Types: deb\nURIs: http://security.ubuntu.com/ubuntu\nSuites: jammy-security\n"
        "Components: main\nSigned-By: /usr/share/keyrings/ubuntu.gpg\n",
    )
    deb822.chmod(0o640)
    before_mode = deb822.stat().st_mode
    unrelated = put(
        tmp_path, "etc/apt/sources.list.d/vendor.list", "deb https://vendor.test/repo stable main\n"
    )
    key = put(tmp_path, "usr/share/keyrings/ubuntu.gpg", "unchanged signing key fixture")
    expected_key, expected_unrelated = key.read_bytes(), unrelated.read_bytes()
    changed = apt.configure(tmp_path)
    assert set(changed) == {
        "etc/apt/apt-mirrors.txt",
        "etc/apt/sources.list.d/ubuntu.sources",
        "etc/apt/apt.conf.d/99-cpm-ci-network",
    }
    assert source.read_bytes() == original_source
    assert mirror.read_text() == "https://archive.ubuntu.com/ubuntu\tpriority:1\n"
    assert deb822.stat().st_mode == before_mode
    assert key.read_bytes() == expected_key and unrelated.read_bytes() == expected_unrelated
    config = tmp_path / "etc/apt/apt.conf.d/99-cpm-ci-network"
    assert config.read_text() == apt.NETWORK_CONFIG
    assert "AllowInsecure" not in config.read_text() and "Verify-Peer" not in config.read_text()
    mtimes = {p: p.stat().st_mtime_ns for p in (source, mirror, deb822, unrelated, key, config)}
    assert apt.configure(tmp_path) == []
    assert {p: p.stat().st_mtime_ns for p in mtimes} == mtimes


@pytest.mark.parametrize(
    "name", ["sources.list", "apt-mirrors.txt", "sources.list.d", "apt.conf.d"]
)
def test_linked_inputs_are_refused_before_any_write(tmp_path, name):
    source = put(
        tmp_path,
        "root/etc/apt/sources.list",
        "deb http://azure.archive.ubuntu.com/ubuntu jammy main\n",
    )
    before = source.read_bytes()
    target = tmp_path / "root/etc/apt" / name
    outside = tmp_path / "outside"
    directory = name.endswith(".d")
    if directory:
        outside.mkdir()
    else:
        outside.write_text("http://azure.archive.ubuntu.com/ubuntu\n")
    if target.exists():
        target.unlink()
    try:
        target.symlink_to(outside, target_is_directory=directory)
    except OSError:
        pytest.skip("Creating symlinks requires OS support/privilege")
    with pytest.raises(ValueError, match="linked"):
        apt.configure(tmp_path / "root")
    if name != "sources.list":
        assert source.read_bytes() == before
    assert not (tmp_path / "root/etc/apt/apt.conf.d/99-cpm-ci-network").exists()
    if not directory:
        assert outside.read_text() == "http://azure.archive.ubuntu.com/ubuntu\n"


def test_hardlinked_source_is_refused(tmp_path):
    source = put(
        tmp_path,
        "root/etc/apt/sources.list",
        "deb http://azure.archive.ubuntu.com/ubuntu jammy main\n",
    )
    outside = tmp_path / "outside"
    before = source.read_bytes()
    try:
        os.link(source, outside)
    except OSError:
        pytest.skip("Hard links require filesystem support")
    with pytest.raises(ValueError, match="unshared"):
        apt.configure(tmp_path / "root")
    assert source.read_bytes() == outside.read_bytes() == before
    assert not (tmp_path / "root/etc/apt/apt.conf.d").exists()


def test_invalid_encoding_does_not_partially_rewrite_sources(tmp_path):
    source = put(
        tmp_path, "etc/apt/sources.list", "deb http://azure.archive.ubuntu.com/ubuntu jammy main\n"
    )
    before = source.read_bytes()
    (tmp_path / "etc/apt/apt-mirrors.txt").write_bytes(b"\xffprivate")
    with pytest.raises(UnicodeError):
        apt.configure(tmp_path)
    assert source.read_bytes() == before
    assert not (tmp_path / "etc/apt/apt.conf.d").exists()


def test_cli_requires_an_explicit_root_and_does_not_echo_private_errors(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["configure_ci_apt.py"])
    with pytest.raises(SystemExit) as failure:
        apt.main()
    assert failure.value.code == 2
    capsys.readouterr()

    def failed(_root):
        raise OSError("private-source-password")

    monkeypatch.setattr(apt, "configure", failed)
    monkeypatch.setattr("sys.argv", ["configure_ci_apt.py", "--root", "/fixture"])
    with pytest.raises(SystemExit) as failure:
        apt.main()
    assert failure.value.code == 1
    assert "private-source-password" not in capsys.readouterr().err
