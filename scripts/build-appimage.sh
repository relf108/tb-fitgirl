#!/usr/bin/env bash
# Build a single-file AppImage: Flutter GUI + frozen Python CLI/bridge.
#
# Prerequisites (build host):
#   - flutter (linux desktop enabled)
#   - python3 >= 3.10 with venv + pip
#   - network (pip deps + appimagetool on first run)
#
# Output: dist/tb-fitgirl-<version>-<arch>.AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(
  python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])" \
    2>/dev/null \
  || python3 -c "import re; print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))"
)"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH=x86_64 ;;
  aarch64|arm64) ARCH=aarch64 ;;
esac

DIST_DIR="${ROOT}/dist"
BUILD_DIR="${ROOT}/build/appimage"
APPDIR="${BUILD_DIR}/AppDir"
CACHE_DIR="${BUILD_DIR}/cache"
VENV="${BUILD_DIR}/venv"
OUT="${DIST_DIR}/tb-fitgirl-${VERSION}-${ARCH}.AppImage"

APPIMAGETOOL_URL_x86_64="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL_URL_aarch64="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-aarch64.AppImage"

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

need python3
need flutter

mkdir -p "$DIST_DIR" "$CACHE_DIR"
rm -rf "$APPDIR"
mkdir -p \
  "$APPDIR/usr/bin" \
  "$APPDIR/usr/opt/tbfg_gui" \
  "$APPDIR/usr/share/applications" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# --- Flutter release bundle -------------------------------------------------
log "Building Flutter Linux release"
(
  cd gui
  if [[ ! -f linux/CMakeLists.txt ]]; then
    flutter create --platforms=linux --project-name tbfg_gui --no-pub .
  fi
  flutter pub get
  flutter build linux --release
)

BUNDLE=""
# Flutter uses x64 / arm64; appimagetool uses x86_64 / aarch64.
for candidate in \
  "gui/build/linux/x64/release/bundle" \
  "gui/build/linux/arm64/release/bundle" \
  "gui/build/linux/${ARCH}/release/bundle" \
  "gui/build/linux/release/bundle"
do
  if [[ -x "${ROOT}/${candidate}/tbfg_gui" ]]; then
    BUNDLE="${ROOT}/${candidate}"
    break
  fi
done
[[ -n "$BUNDLE" ]] || die "Flutter bundle not found under gui/build/linux/"

log "Staging Flutter bundle from ${BUNDLE#"$ROOT"/}"
cp -a "$BUNDLE/." "$APPDIR/usr/opt/tbfg_gui/"
# Thin launcher: keep Flutter's lib/ + data/ next to the binary, put
# bundled bridge on PATH for the GUI.
cat >"$APPDIR/usr/bin/tbfg_gui" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
BUNDLE="$(cd "$HERE/../opt/tbfg_gui" && pwd)"
export LD_LIBRARY_PATH="${BUNDLE}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="${HERE}:${PATH}"
exec "${BUNDLE}/tbfg_gui" "$@"
EOF
chmod +x "$APPDIR/usr/bin/tbfg_gui"

# --- Frozen Python CLI + bridge (no host Python at runtime) -----------------
log "Freezing Python CLI/bridge with PyInstaller"
rm -rf "$VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install "$ROOT" pyinstaller

# Tiny entry stubs so PyInstaller has a clear script target.
ENTRY_DIR="${BUILD_DIR}/pyi-entry"
mkdir -p "$ENTRY_DIR"
cat >"${ENTRY_DIR}/tb_fitgirl_cli.py" <<'EOF'
from tb_fitgirl.cli import main
raise SystemExit(main())
EOF
cat >"${ENTRY_DIR}/tb_fitgirl_bridge.py" <<'EOF'
from tb_fitgirl.bridge import cli_main
cli_main()
EOF

PYI_COMMON=(
  --noconfirm
  --clean
  --onefile
  --collect-submodules tb_fitgirl
  --collect-data certifi
  --distpath "${BUILD_DIR}/pyi-dist"
  --workpath "${BUILD_DIR}/pyi-work"
  --specpath "${BUILD_DIR}/pyi-spec"
)

mkdir -p "${BUILD_DIR}/pyi-dist"
pyinstaller "${PYI_COMMON[@]}" --name tb-fitgirl "${ENTRY_DIR}/tb_fitgirl_cli.py"
pyinstaller "${PYI_COMMON[@]}" --name tb-fitgirl-bridge "${ENTRY_DIR}/tb_fitgirl_bridge.py"

deactivate

# Process-group wrapper: Flutter kills -TERM -- -<pid>. PyInstaller's
# bootloader is a parent of the Python process, so setpgid inside Python
# only moves the child. Own the group in a thin native wrapper, then exec
# the frozen binary (same PID after exec → bootloader + descendants share
# the group). Prefer a static binary so builds on NixOS don't embed a
# /nix/store dynamic linker (unusable on generic FHS hosts).
need cc
log "Building pgrp-exec wrapper"
PGRP_BIN="${BUILD_DIR}/pgrp-exec"
PGRP_SRC="$ROOT/packaging/appimage/pgrp-exec.c"
build_pgrp_exec() {
  # Static first (portable).
  if cc -O2 -s -static -o "$PGRP_BIN" "$PGRP_SRC" 2>/dev/null; then
    return 0
  fi
  if command -v musl-gcc >/dev/null 2>&1 \
    && musl-gcc -O2 -s -static -o "$PGRP_BIN" "$PGRP_SRC" 2>/dev/null; then
    return 0
  fi
  # NixOS: pkgsStatic produces a musl-static binary without host glibc.
  if [[ -e /etc/NIXOS ]] && command -v nix-build >/dev/null 2>&1; then
    local store
    # Nix path literal (absolute, no spaces). Do not bash-@Q — single
    # quotes are invalid in Nix and silently kill this fallback.
    store="$(
      nix-build --no-out-link -E "
        with import <nixpkgs> {};
        pkgsStatic.stdenv.mkDerivation {
          name = \"pgrp-exec\";
          src = ${PGRP_SRC};
          dontUnpack = true;
          buildPhase = \"\$CC -O2 -s -static -o pgrp-exec \$src\";
          installPhase = \"mkdir -p \$out/bin; cp pgrp-exec \$out/bin/\";
        }
      "
    )" || true
    if [[ -n "${store:-}" && -x "${store}/bin/pgrp-exec" ]]; then
      cp "${store}/bin/pgrp-exec" "$PGRP_BIN"
      return 0
    fi
  fi
  # Dynamic against host libc — only OK with an FHS loader path.
  cc -O2 -s -o "$PGRP_BIN" "$PGRP_SRC" || die "could not compile pgrp-exec"
  local interp
  interp="$(
    readelf -l "$PGRP_BIN" 2>/dev/null \
      | sed -n 's/.*Requesting program interpreter: \([^]]*\)].*/\1/p'
  )"
  case "$interp" in
    /lib/ld-linux*.so* | /lib64/ld-linux*.so* | /lib/ld-musl*.so* | \
    /lib64/ld-musl*.so*) ;;
    *)
      die "pgrp-exec interpreter is non-FHS (${interp:-unknown}); need static libc or musl-gcc"
      ;;
  esac
}
build_pgrp_exec
chmod +x "$PGRP_BIN"

install -m 755 "${BUILD_DIR}/pyi-dist/tb-fitgirl" \
  "$APPDIR/usr/bin/tb-fitgirl.bin"
install -m 755 "${BUILD_DIR}/pyi-dist/tb-fitgirl-bridge" \
  "$APPDIR/usr/bin/tb-fitgirl-bridge.bin"
# Shell launchers next to the binaries so relative paths stay inside AppDir.
cat >"$APPDIR/usr/bin/tb-fitgirl" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "${HERE}/pgrp-exec" "${HERE}/tb-fitgirl.bin" "$@"
EOF
cat >"$APPDIR/usr/bin/tb-fitgirl-bridge" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "${HERE}/pgrp-exec" "${HERE}/tb-fitgirl-bridge.bin" "$@"
EOF
install -m 755 "$PGRP_BIN" "$APPDIR/usr/bin/pgrp-exec"
chmod +x "$APPDIR/usr/bin/tb-fitgirl" "$APPDIR/usr/bin/tb-fitgirl-bridge"

# --- Desktop metadata + icon ------------------------------------------------
log "Installing desktop entry and icon"
cp "$ROOT/packaging/appimage/tbfg_gui.desktop" "$APPDIR/tbfg_gui.desktop"
cp "$ROOT/packaging/appimage/tbfg_gui.desktop" \
  "$APPDIR/usr/share/applications/tbfg_gui.desktop"
sed -i "s/^X-AppImage-Version=.*/X-AppImage-Version=${VERSION}/" \
  "$APPDIR/tbfg_gui.desktop" \
  "$APPDIR/usr/share/applications/tbfg_gui.desktop"

ICON_DST="$APPDIR/tbfg_gui.png"
ICON_SRC="$ROOT/packaging/appimage/tbfg_gui.png"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$ICON_DST"
else
  log "Generating placeholder icon"
  python3 - "$ICON_DST" <<'PY'
import struct
import sys
import zlib
from pathlib import Path

out = Path(sys.argv[1])
w = h = 256
r, g, b, a = 0x5C, 0x6B, 0xC0, 0xFF
raw = b"".join(b"\x00" + bytes([r, g, b, a]) * w for _ in range(h))
comp = zlib.compress(raw, 9)

def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(
        ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
    )

png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", comp)
png += chunk(b"IEND", b"")
out.write_bytes(png)
PY
fi
cp "$ICON_DST" "$APPDIR/usr/share/icons/hicolor/256x256/apps/tbfg_gui.png"

cp "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# --- appimagetool -----------------------------------------------------------
TOOL="${CACHE_DIR}/appimagetool-${ARCH}.AppImage"
url_var="APPIMAGETOOL_URL_${ARCH}"
url="${!url_var-}"
[[ -n "$url" ]] || die "no appimagetool URL for arch ${ARCH}"

if [[ ! -x "$TOOL" ]]; then
  log "Downloading appimagetool (${ARCH})"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$TOOL" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$TOOL" "$url"
  else
    die "need curl or wget to download appimagetool"
  fi
  chmod +x "$TOOL"
fi

run_appimagetool() {
  # Prefer no-FUSE extract-and-run (works on NixOS / CI without fuse).
  if "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"; then
    return 0
  fi
  if APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT"; then
    return 0
  fi
  EXTRACT_DIR="${CACHE_DIR}/appimagetool-extracted-${ARCH}"
  if [[ ! -x "${EXTRACT_DIR}/AppRun" ]]; then
    rm -rf "$EXTRACT_DIR"
    (
      cd "$CACHE_DIR"
      if "$TOOL" --appimage-extract >/dev/null 2>&1; then
        mv squashfs-root "$EXTRACT_DIR"
      elif command -v unsquashfs >/dev/null 2>&1; then
        unsquashfs -d "$EXTRACT_DIR" "$TOOL"
      else
        die "could not run appimagetool (no FUSE / extract). Install fuse3 or unsquashfs."
      fi
    )
  fi
  "${EXTRACT_DIR}/AppRun" "$APPDIR" "$OUT"
}

log "Packing AppImage -> ${OUT#"$ROOT"/}"
export ARCH
run_appimagetool
chmod +x "$OUT"
log "Done: $OUT"
ls -lh "$OUT"
