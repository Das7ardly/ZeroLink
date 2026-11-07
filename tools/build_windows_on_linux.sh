#!/usr/bin/env bash
# Build ZeroLink.exe, ZeroLink-Setup-<ver>.exe and ZeroLink-Portable-<ver>.exe on Linux (no Windows PC needed).
# Needs: wine64, nsis, gcc-mingw-w64-x86-64, and a Windows CPython "install_only" tarball from
# https://github.com/astral-sh/python-build-standalone/releases (x86_64-pc-windows-msvc).
# Usage: tools/build_windows_on_linux.sh path/to/cpython-3.12.*-x86_64-pc-windows-msvc-install_only.tar.gz
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c "import re;print(re.search(r'\"(.+)\"', open('zero/__init__.py').read()).group(1))")
WORK=${WORK:-$PWD/.winbuild}
export WINEPREFIX=$WORK/prefix WINEDEBUG=-all
W=$(command -v wine64 || echo /usr/lib/wine/wine64)
mkdir -p "$WORK/wheels" "$WORK/pywin"
[ -x "$WORK/pywin/python/python.exe" ] || tar xzf "$1" -C "$WORK/pywin"
[ -d "$WINEPREFIX" ] || "$W" wineboot -i >/dev/null 2>&1
# Qt6Core.dll links to Windows' own ICU (icuuc.dll); Wine has none. A stub lets PyInstaller import Qt.
x86_64-w64-mingw32-gcc -shared -O2 -o "$WINEPREFIX/drive_c/windows/system32/icuuc.dll" tools/icuuc_stub.c
pip download -q --dest "$WORK/wheels" --platform win_amd64 --python-version 3.12 --implementation cp \
    --only-binary=:all: -r requirements.txt pyinstaller pefile pywin32-ctypes
"$W" "$WORK/pywin/python/python.exe" -m pip install -q --no-index --find-links "$WORK/wheels" \
    -r requirements.txt pyinstaller 2>&1 | cat
"$W" "$WORK/pywin/python/python.exe" -m PyInstaller --noconfirm --clean zerolink.spec 2>&1 | cat > "$WORK/pyinstaller.log"
makensis -V2 -DVERSION="$VER" installer.nsi
makensis -V2 -DVERSION="$VER" portable.nsi
ls -la "ZeroLink-Setup-$VER.exe" "ZeroLink-Portable-$VER.exe"
