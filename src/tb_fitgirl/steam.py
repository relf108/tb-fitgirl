"""Steam library discovery and non-Steam shortcut management (Linux-first).

Shortcuts are written to userdata/<uid>/config/shortcuts.vdf (binary VDF)
so installed games appear in Steam, where launch options and a Proton
version can be set via Properties. Steam reads the file on startup and
rewrites it on exit, so it must be closed when we write.
"""

from __future__ import annotations

import re
import shutil
import struct
import time
import zlib
from pathlib import Path
from typing import Any

STEAM_ROOTS = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",  # flatpak
)


class SteamNotFound(RuntimeError):
    pass


def steam_root() -> Path:
    for candidate in STEAM_ROOTS:
        path = Path(candidate).expanduser()
        if (path / "steamapps").is_dir():
            return path.resolve()
    raise SteamNotFound("No Steam installation found (looked in: " + ", ".join(STEAM_ROOTS) + ")")


def library_paths(root: Path | None = None) -> list[Path]:
    """All Steam library folders, first entry is the main one."""
    root = root or steam_root()
    vdf = root / "steamapps" / "libraryfolders.vdf"
    paths: list[Path] = []
    if vdf.is_file():
        # Cheap VDF parse: "path"  "..." lines. Good enough for library folders.
        for match in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
            path = Path(match.group(1))
            if (path / "steamapps").is_dir() and path not in paths:
                paths.append(path)
    if not paths:
        paths.append(root)
    return paths


def common_dir(root: Path | None = None) -> Path:
    """steamapps/common of the main library (created if missing)."""
    common = library_paths(root)[0] / "steamapps" / "common"
    common.mkdir(parents=True, exist_ok=True)
    return common


def proton_installs(root: Path | None = None) -> dict[str, Path]:
    """Discover Proton runtimes: {display_name: proton_script_path}.

    Covers Valve's official Protons in steamapps/common and custom builds
    (Proton-GE, cachyos, etc.) in compatibilitytools.d.
    """
    root = root or steam_root()
    found: dict[str, Path] = {}
    for library in library_paths(root):
        for d in sorted((library / "steamapps" / "common").glob("Proton*")):
            script = d / "proton"
            if script.is_file():
                found[d.name] = script
    for d in sorted((root / "compatibilitytools.d").glob("*")):
        script = d / "proton"
        if script.is_file():
            found[d.name] = script
    return found


# Steam Linux Runtime app ids -> directory names under steamapps/common.
_SLR_APPIDS = {
    "1391110": "SteamLinuxRuntime",  # scout (legacy)
    "1628350": "SteamLinuxRuntime_soldier",
    "1070560": "SteamLinuxRuntime",  # scout compat
    "4183110": "SteamLinuxRuntime_sniper",
    "1852090": "SteamLinuxRuntime_sniper",
}


def _read_require_tool_appid(proton_script: Path) -> str | None:
    manifest = proton_script.parent / "toolmanifest.vdf"
    if not manifest.is_file():
        return None
    match = re.search(r'"require_tool_appid"\s+"(\d+)"', manifest.read_text(errors="replace"))
    return match.group(1) if match else None


def runtime_entry_point(proton_script: Path, root: Path | None = None) -> Path | None:
    """The Steam Linux Runtime _v2-entry-point a Proton requires, if any.

    Running the installer through this container gives Proton the system
    libraries (libvulkan etc.) it needs, independent of the calling shell.
    """
    appid = _read_require_tool_appid(proton_script)
    if appid is None:
        return None
    root = root or steam_root()
    dir_name = _SLR_APPIDS.get(appid)
    candidates = [dir_name] if dir_name else []
    # Fall back to sniper if the appid is unknown but a runtime exists.
    candidates += ["SteamLinuxRuntime_sniper", "SteamLinuxRuntime_soldier"]
    for library in library_paths(root):
        for name in candidates:
            entry = library / "steamapps" / "common" / name / "_v2-entry-point"
            if entry.is_file():
                return entry
    return None


def _proton_version_key(name: str) -> tuple[int, float]:
    """Sort key for official Protons: higher numeric version wins."""
    match = re.search(r"(\d+(?:\.\d+)?)", name)
    return (int(float(match.group(1))) if match else 0, 0.0)


def newest_proton(root: Path | None = None) -> Path:
    """Best default Proton.

    Prefer Valve's official numbered Proton (e.g. 'Proton 11.0'): unlike some
    custom builds (Proton-GE, cachyos) it stays compatible with the bundled
    Steam Linux Runtime Python, which those custom builds can outrun. Custom
    builds remain selectable explicitly via find_proton().
    """
    installs = proton_installs(root)
    if not installs:
        raise SteamNotFound("No Proton runtimes found. Install one via Steam.")
    official = {
        name: p
        for name, p in installs.items()
        if re.fullmatch(r"Proton \d.*", name) and "Experimental" not in name
    }
    if official:
        newest = max(official, key=_proton_version_key)
        return official[newest]
    # No official numbered build: fall back to whatever exists (newest on disk).
    return max(installs.values(), key=lambda p: p.parent.stat().st_mtime)


def find_proton(name_substring: str, root: Path | None = None) -> Path:
    """Locate a Proton install by (case-insensitive) name substring."""
    installs = proton_installs(root)
    needle = name_substring.lower()
    for name, path in installs.items():
        if needle in name.lower():
            return path
    available = ", ".join(installs) or "none"
    raise SteamNotFound(f"No Proton matching '{name_substring}'. Available: {available}")


def shortcuts_vdf(root: Path | None = None) -> Path:
    """Path to shortcuts.vdf of the most recently used Steam user."""
    root = root or steam_root()
    users = [p for p in (root / "userdata").glob("[0-9]*") if p.is_dir()]
    if not users:
        raise SteamNotFound(f"No Steam users under {root / 'userdata'}")
    user = max(users, key=lambda p: p.stat().st_mtime)
    config = user / "config"
    config.mkdir(exist_ok=True)
    return config / "shortcuts.vdf"


# -- binary VDF (types: 0x00 nested map, 0x01 utf-8 string, 0x02 uint32) -----

_MAP, _STRING, _INT = 0x00, 0x01, 0x02
_END = 0x08


def _read_map(buf: bytes, pos: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while True:
        kind = buf[pos]
        pos += 1
        if kind == _END:
            return out, pos
        end = buf.index(b"\x00", pos)
        key = buf[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        if kind == _MAP:
            out[key], pos = _read_map(buf, pos)
        elif kind == _STRING:
            end = buf.index(b"\x00", pos)
            out[key] = buf[pos:end].decode("utf-8", errors="replace")
            pos = end + 1
        elif kind == _INT:
            out[key] = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise ValueError(f"Unknown VDF field type 0x{kind:02x} at offset {pos - 1}")


def _write_map(data: dict[str, Any]) -> bytes:
    out = bytearray()
    for key, value in data.items():
        if isinstance(value, dict):
            out += bytes([_MAP]) + key.encode() + b"\x00" + _write_map(value)
        elif isinstance(value, int):
            out += bytes([_INT]) + key.encode() + b"\x00" + struct.pack("<I", value)
        else:
            out += bytes([_STRING]) + key.encode() + b"\x00" + str(value).encode() + b"\x00"
    out.append(_END)
    return bytes(out)


def load_shortcuts(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return {"shortcuts": {}}
    data, _ = _read_map(path.read_bytes(), 0)
    return data if "shortcuts" in data else {"shortcuts": data}


def steam_running() -> bool:
    """True if a Steam client process is running (it would clobber our writes)."""
    import subprocess

    return (
        subprocess.run(["pgrep", "-x", "steam"], capture_output=True, check=False).returncode == 0
    )


def shortcut_appid(exe: str, name: str) -> int:
    """Steam's appid derivation for non-Steam games."""
    return zlib.crc32((exe + name).encode()) | 0x80000000


def add_shortcut(
    name: str,
    exe: Path,
    *,
    start_dir: Path | None = None,
    launch_options: str = "",
    icon: Path | str | None = None,
    vdf_path: Path | None = None,
) -> int:
    """Append a non-Steam game (idempotent by name+exe). Returns its appid.

    Backs up the existing file to shortcuts.vdf.bak first.
    """
    vdf_path = vdf_path or shortcuts_vdf()
    data = load_shortcuts(vdf_path)
    shortcuts: dict[str, Any] = data["shortcuts"]

    exe_quoted = f'"{exe}"'
    appid = shortcut_appid(exe_quoted, name)
    for entry in shortcuts.values():
        if isinstance(entry, dict) and entry.get("appid") == appid:
            return appid  # already present

    index = str(max((int(k) for k in shortcuts if k.isdigit()), default=-1) + 1)
    shortcuts[index] = {
        "appid": appid,
        "AppName": name,
        "Exe": exe_quoted,
        "StartDir": f'"{start_dir or exe.parent}"',
        "icon": str(icon) if icon else "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": int(time.time()),
        "FlatpakAppID": "",
        "tags": {},
    }

    if vdf_path.is_file():
        shutil.copy2(vdf_path, vdf_path.with_suffix(".vdf.bak"))
    vdf_path.write_bytes(_write_map(data))
    return appid


def grid_dir(root: Path | None = None) -> Path:
    """Custom-artwork directory for the most recently used Steam user."""
    return shortcuts_vdf(root).parent / "grid"


def set_grid_art(appid: int, image: Path, *, grid: Path | None = None) -> Path:
    """Install *image* as a shortcut's header (wide capsule) in Steam's grid.

    Steam reads userdata/<uid>/config/grid/<appid>.<ext> as the custom
    horizontal capsule for non-Steam shortcuts. Read on startup like
    shortcuts.vdf, so Steam should be closed. Returns the path written.
    """
    directory = grid if grid is not None else grid_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{appid}{image.suffix.lower() or '.jpg'}"
    for stale in directory.glob(f"{appid}.*"):
        if stale != target:
            stale.unlink()
    shutil.copy2(image, target)
    return target


def remove_grid_art(appid: int, *, grid: Path | None = None) -> bool:
    """Delete all custom grid art for *appid* (capsule/p/hero/logo variants)."""
    directory = grid if grid is not None else grid_dir()
    if not directory.is_dir():
        return False
    removed = False
    for path in directory.glob(f"{appid}*.*"):
        path.unlink()
        removed = True
    return removed


def remove_shortcut(name: str, *, vdf_path: Path | None = None) -> int | None:
    """Remove non-Steam shortcut(s) whose AppName matches *name*.

    Returns the appid of a removed entry (or None if none matched). Backs up
    to shortcuts.vdf.bak first. Re-indexes remaining entries.
    """
    vdf_path = vdf_path or shortcuts_vdf()
    if not vdf_path.is_file():
        return None
    data = load_shortcuts(vdf_path)
    shortcuts: dict[str, Any] = data["shortcuts"]

    kept: list[dict[str, Any]] = []
    removed_appid: int | None = None
    for entry in shortcuts.values():
        if isinstance(entry, dict) and entry.get("AppName") == name:
            removed_appid = entry.get("appid", removed_appid)
        else:
            kept.append(entry)

    if removed_appid is None:
        return None

    data["shortcuts"] = {str(i): entry for i, entry in enumerate(kept)}
    shutil.copy2(vdf_path, vdf_path.with_suffix(".vdf.bak"))
    vdf_path.write_bytes(_write_map(data))
    return removed_appid
