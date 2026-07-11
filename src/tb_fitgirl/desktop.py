"""Freedesktop .desktop launcher entries (Linux app menus)."""

from __future__ import annotations

from pathlib import Path

APPLICATIONS_DIR = "~/.local/share/applications"


def steam_rungameid(shortcut_appid: int) -> int:
    """Steam's ``rungameid`` value for a non-Steam shortcut.

    For non-Steam games this is a 64-bit value built from the 32-bit shortcut
    appid in the high dword plus a fixed low dword, so ``steam://rungameid/N``
    launches the shortcut (honouring its Proton/launch-option settings).
    """
    return (shortcut_appid << 32) | 0x02000000


def _escape(value: str) -> str:
    # Desktop-entry values: escape backslashes then reserved leading chars.
    return value.replace("\\", "\\\\")


def write_desktop_entry(
    name: str,
    shortcut_appid: int,
    *,
    applications_dir: Path | str = APPLICATIONS_DIR,
    icon: str | None = None,
    comment: str = "Play this game on Steam",
) -> Path:
    """Write a .desktop file launching the non-Steam shortcut via Steam.

    Returns the path written. Launching through Steam (not the exe directly)
    means the game runs under the Proton version and launch options configured
    for the shortcut, matching how it behaves from the Steam library.
    """
    directory = Path(applications_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    rungameid = steam_rungameid(shortcut_appid)
    lines = [
        "[Desktop Entry]",
        f"Name={_escape(name)}",
        f"Comment={_escape(comment)}",
        f"Exec=steam steam://rungameid/{rungameid}",
        f"Icon={icon or f'steam_icon_{shortcut_appid}'}",
        "Terminal=false",
        "Type=Application",
        "Categories=Game;",
        f"StartupWMClass={_escape(name)}",
        "",
    ]
    # Sanitise the filename but keep it recognisable.
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    path = directory / f"tb-fitgirl-{safe}.desktop"
    path.write_text("\n".join(lines))
    path.chmod(0o755)
    return path
