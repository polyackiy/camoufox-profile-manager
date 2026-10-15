#!/usr/bin/env bash
# Destructive package lifecycle test ONLY inside a disposable Ubuntu container.
# Inputs are this CI job's trusted .deb, never third-party/user-uploaded packages.
set -euo pipefail

if [[ ${CPM_DISPOSABLE_PACKAGE_TEST:-} != 1 || ! -f /.dockerenv || $EUID != 0 ]]; then
    echo 'Requires an explicitly opted-in disposable root Docker container.' >&2
    exit 2
fi
package=$(realpath "${1:?Pass the built .deb}")
report=${2:?Pass the JSON report path}
[[ $(dpkg-deb -f "$package" Package) == camoufox-pm ]]
if dpkg-query -W camoufox-pm >/dev/null 2>&1 || id cpm-package-test >/dev/null 2>&1; then
    echo 'Refusing to test over an existing installation/account.' >&2
    exit 2
fi
# No build toolchain or language runtime is installed by the harness.
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "$package"
if command -v python3 || command -v node; then
    echo "Runtime container unexpectedly contains a development runtime." >&2
    exit 1
fi
[[ $(dpkg-query -W -f='${Status}' camoufox-pm) == 'install ok installed' ]]
[[ $(readlink /usr/bin/camoufox-pm) == /opt/camoufox-pm/camoufox-pm ]]
[[ -s /usr/share/applications/camoufox-pm.desktop ]]
[[ -s /usr/share/icons/hicolor/scalable/apps/camoufox-pm.svg ]]
# Package dependencies must be sufficient for the bundled Qt GUI as well as the
# frozen executable. PyInstaller uses _internal as its native library directory.
internal=/opt/camoufox-pm/_internal
checked=0
while IFS= read -r -d '' library; do
    dependencies=$(LD_LIBRARY_PATH="$internal" ldd "$library")
    if [[ $dependencies == *'not found'* ]]; then
        printf 'Missing runtime dependency in %s:\n%s\n' "$library" "$dependencies" >&2
        exit 1
    fi
    checked=$((checked + 1))
done < <(find "$internal" -type f \( -name libqxcb.so -o -name libQt6WebEngineCore.so.6 -o -name QtWebEngineProcess \) -print0)
[[ $checked -ge 3 ]]

useradd --create-home --shell /bin/bash cpm-package-test
home_dir=/home/cpm-package-test
smoke() {
    # Run the installed launcher as an ordinary user with a minimal environment;
    # no source checkout, virtualenv, Python or Node path can satisfy imports.
    timeout 60 runuser -u cpm-package-test -- env -i HOME="$home_dir" PATH=/usr/bin:/bin \
        CPM_DB_PATH="$home_dir/recovery/profiles.db" \
        /usr/bin/camoufox-pm --desktop update-recovery status > /tmp/cpm-recovery-status
    grep -Eq '"state"[[:space:]]*:[[:space:]]*"idle"' /tmp/cpm-recovery-status
    grep -Eq '"automatic_install_available"[[:space:]]*:[[:space:]]*false' /tmp/cpm-recovery-status
    [[ ! -e "$home_dir/recovery/profiles.db" && ! -e "$home_dir/recovery/secret.key" ]]
}
smoke
# Synthetic user data plus an unowned file in the package directory must survive
# reinstall, remove and purge. No real profile or encryption key is used.
runuser -u cpm-package-test -- mkdir -p "$home_dir/.local/share/camoufox-profile-manager/profiles/example"
printf 'synthetic profile database\n' > "$home_dir/.local/share/camoufox-profile-manager/profiles.db"
printf 'synthetic key\n' > "$home_dir/.local/share/camoufox-profile-manager/secret.key"
printf 'synthetic browser data\n' > "$home_dir/.local/share/camoufox-profile-manager/profiles/example/cookies.sqlite"
printf 'unowned file\n' > /opt/camoufox-pm/keep-user-file.txt
chown -R cpm-package-test:cpm-package-test "$home_dir/.local"
find "$home_dir/.local" -type f -exec sha256sum {} + > /tmp/cpm-user-checksums
sha256sum /opt/camoufox-pm/keep-user-file.txt >> /tmp/cpm-user-checksums
preserved() { sha256sum --check /tmp/cpm-user-checksums; }
apt-get install -y --reinstall "$package"
smoke
preserved
apt-get remove -y camoufox-pm
[[ ! -e /usr/bin/camoufox-pm && ! -e /opt/camoufox-pm/camoufox-pm ]]
preserved
apt-get install -y "$package"
smoke
preserved
apt-get purge -y camoufox-pm
[[ ! -e /usr/bin/camoufox-pm && ! -e /usr/share/applications/camoufox-pm.desktop ]]
[[ ! -e /opt/camoufox-pm/camoufox-pm ]]
preserved
# shellcheck source=/dev/null
. /etc/os-release
checksum=$(sha256sum "$package")
checksum=${checksum%% *}
# Fixed-format metadata only: no user data, paths, environment or logs uploaded.
printf '{"passed":true,"ubuntu":"%s","sha256":"%s","qt_dependencies_checked":%d,"scope":"APT install/reinstall/remove/purge and non-root frozen recovery; not GUI/browser acceptance"}\n' \
    "$VERSION_ID" "$checksum" "$checked" > "$report"
