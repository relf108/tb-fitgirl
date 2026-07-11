"""Install FitGirl repacks on Linux via Wine.

FitGirl repacks follow a stable layout:

    <Repack>/
    ├── setup.exe                    InnoSetup-based installer
    ├── fg-01.bin ... fg-NN.bin      required archives
    ├── fg-optional-*.bin            optional components (soundtracks etc)
    └── MD5/fitgirl-bins.md5         checksums for all of the above

The installer honours standard InnoSetup silent flags, which is what
makes one-click installs possible.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WINE_PREFIX = "~/.tb-fitgirl/wine"


class InstallError(RuntimeError):
    pass


@dataclass
class RepackDir:
    """A validated FitGirl repack directory."""

    path: Path
    setup_exe: Path
    bins: list[Path] = field(default_factory=list)
    optional_bins: list[Path] = field(default_factory=list)
    md5_file: Path | None = None

    @property
    def game_name(self) -> str:
        """'DELTARUNE [FitGirl Repack]' -> 'DELTARUNE'."""
        name = self.path.name
        for marker in ("[FitGirl", "(FitGirl"):
            idx = name.find(marker)
            if idx != -1:
                name = name[:idx]
        return name.strip(" -_")

    @property
    def archive_bytes(self) -> int:
        """Total size of all (required + optional) archives on disk."""
        return sum(p.stat().st_size for p in (*self.bins, *self.optional_bins))


# InstallProgress(bytes_written, estimated_total_or_None, elapsed_seconds, bytes_per_sec)
# bytes_per_sec is a rolling rate over the last sample interval, not a
# cumulative average (which is skewed low by the slow prefix-init phase).
InstallProgressFn = Callable[[int, int | None, float, float], None]


def estimate_installed_size(repack: RepackDir) -> int | None:
    """Rough expanded size for a progress denominator.

    FitGirl archives are heavily recompressed; expansion is typically
    ~1.5-2.5x the archive size. We use 2x as a coarse estimate. Returns
    None if there are no archives to base it on.
    """
    total = repack.archive_bytes
    return int(total * 2) if total else None


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def find_repack(path: Path | str) -> RepackDir:
    """Validate *path* as a FitGirl repack directory."""
    path = Path(path).expanduser()
    setup = path / "setup.exe"
    if not setup.is_file():
        raise InstallError(f"No setup.exe in {path}")
    bins = sorted(p for p in path.glob("fg-*.bin") if not p.name.startswith("fg-optional"))
    optional = sorted(path.glob("fg-optional-*.bin"))
    if not bins:
        raise InstallError(f"setup.exe found but no fg-*.bin archives in {path}")
    md5 = path / "MD5" / "fitgirl-bins.md5"
    return RepackDir(
        path=path,
        setup_exe=setup,
        bins=bins,
        optional_bins=optional,
        md5_file=md5 if md5.is_file() else None,
    )


def verify_bins(
    repack: RepackDir, on_progress: Callable[[str, bool], None] | None = None
) -> list[str]:
    """Check bins against the shipped md5 file. Returns list of failures."""
    if repack.md5_file is None:
        raise InstallError("No MD5/fitgirl-bins.md5 file to verify against.")
    failures: list[str] = []
    for line in repack.md5_file.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        # Format: <32 hex> *<filename>
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 32:
            continue
        expected, filename = parts[0].lower(), parts[1].lstrip("*").strip()
        # Entries are Windows-style paths relative to the md5 file's directory,
        # e.g. "..\fg-01.bin". Resolve, but never allow escaping the repack dir.
        relative = filename.replace("\\", "/")
        target = (repack.md5_file.parent / relative).resolve()
        if not target.is_relative_to(repack.path.resolve()):
            failures.append(f"{filename}: outside repack directory")
            continue
        if not target.is_file():
            failures.append(f"{filename}: missing")
            if on_progress:
                on_progress(filename, False)
            continue
        digest = hashlib.md5()
        with target.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        ok = digest.hexdigest() == expected
        if not ok:
            failures.append(f"{filename}: checksum mismatch")
        if on_progress:
            on_progress(filename, ok)
    return failures


def _to_wine_path(path: Path) -> str:
    """Absolute unix path -> wine Z: drive path."""
    return "Z:" + str(path).replace("/", "\\")


def _mute_env(env: dict[str, str]) -> None:
    """Disable Wine's audio drivers so the installer's music is silenced."""
    env["WINEDLLOVERRIDES"] = ";".join(
        filter(None, [env.get("WINEDLLOVERRIDES"), "winealsa.drv,winepulse.drv=d"])
    )


def _wine_command(
    repack: RepackDir, target_dir: Path, setup_args: list[str], prefix: Path, mute: bool
) -> tuple[list[str], dict[str, str]]:
    prefix.mkdir(parents=True, exist_ok=True)  # wine won't create missing parents
    cmd = ["wine", str(repack.setup_exe), *setup_args]
    env = dict(os.environ)
    env["WINEPREFIX"] = str(prefix)
    env.setdefault("WINEDEBUG", "-all")
    if mute:
        _mute_env(env)
    return cmd, env


def _proton_command(
    repack: RepackDir,
    target_dir: Path,
    setup_args: list[str],
    prefix: Path,
    mute: bool,
    proton: Path,
    use_steam_run: bool = True,
) -> tuple[list[str], dict[str, str]]:
    from . import steam

    # Proton manages the prefix itself under <compat_data>/pfx.
    prefix.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["STEAM_COMPAT_DATA_PATH"] = str(prefix)
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam.steam_root())
    env.setdefault("WINEDEBUG", "-all")
    if mute:
        _mute_env(env)

    # Invoke the proton script directly (not via an explicit python3): its
    # shebang selects the correct interpreter, and inside the SLR container
    # that is the runtime's bundled Python, not the caller's.
    # 'waitforexitandrun' (not 'run') is the verb Steam uses to launch and
    # wait for a program; it also enables Proton's protonfixes.
    proton_cmd = [str(proton), "waitforexitandrun", str(repack.setup_exe), *setup_args]

    # Modern Proton requires a Steam Linux Runtime container, which also
    # supplies system libs (libvulkan etc.) that a hermetic shell may lack.
    entry = steam.runtime_entry_point(proton)
    if entry is not None:
        cmd = [str(entry), "--verb=run", "--", *proton_cmd]
    else:
        cmd = proton_cmd

    # On NixOS the Steam runtime's dynamically-linked binaries (pressure-vessel)
    # can't run without an FHS environment. steam-run provides one; it's a
    # no-op elsewhere because it simply won't be on PATH.
    steam_run = _steam_run_wrapper() if use_steam_run else None
    if steam_run:
        cmd = [steam_run, *cmd]
    return cmd, env


def _steam_run_wrapper() -> str | None:
    """Path to steam-run (NixOS FHS wrapper) if available, else None."""
    return shutil.which("steam-run")


def install(
    repack: RepackDir,
    target_dir: Path,
    *,
    runtime: str = "wine",
    proton: Path | str | None = None,
    use_steam_run: bool = True,
    wine_prefix: Path | str = DEFAULT_WINE_PREFIX,
    silent: bool = True,
    mute: bool = True,
    ready_when: Callable[[Path], bool] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    on_progress: InstallProgressFn | None = None,
    poll_interval: float = 1.0,
) -> Path:
    """Run the repack installer via *runtime* ("wine" or "proton").

    Silent installs use InnoSetup's /SILENT with all default components.

    Proton is more reliable for FitGirl's unpacker. If *on_progress* is given,
    a background thread polls the growing target directory and reports
    (bytes, estimated_total, elapsed, bytes_per_sec). Returns the target dir.
    """
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    setup_args: list[str] = []
    if silent:
        # /SILENT (not /VERYSILENT): suppresses the wizard pages but keeps the
        # progress window. FitGirl's ISDone/unarc unpacker drives its progress
        # via that window's message pump; under /VERYSILENT the pump is gone and
        # the unpack thread deadlocks (observed hanging at the first big file).
        setup_args += ["/SILENT", "/NORESTART", "/SP-"]
    setup_args.append(f"/DIR={_to_wine_path(target_dir)}")

    prefix = Path(wine_prefix).expanduser()
    if runtime == "proton":
        if proton is None:
            raise InstallError("runtime='proton' requires a proton script path.")
        cmd, env = _proton_command(
            repack, target_dir, setup_args, prefix, mute, Path(proton), use_steam_run
        )
    elif runtime == "wine":
        cmd, env = _wine_command(repack, target_dir, setup_args, prefix, mute)
    else:
        raise InstallError(f"Unknown runtime '{runtime}' (expected 'wine' or 'proton').")

    estimate = estimate_installed_size(repack)
    stop = threading.Event()
    ready = threading.Event()
    started = time.monotonic()

    def _watch() -> None:
        # Poll the growing install dir for progress, and (if configured)
        # signal readiness once the game is usable so we can stop waiting on
        # FitGirl's post-extraction "finalisation" (Windows redists etc. that
        # are irrelevant under Proton and can hang for a long time).
        last_t = started
        last_done = 0
        stable_since: float | None = None
        while not stop.is_set():
            now = time.monotonic()
            done = _dir_size(target_dir)
            dt = now - last_t
            rate = (done - last_done) / dt if dt > 0 else 0.0
            if on_progress is not None:
                on_progress(done, estimate, now - started, max(rate, 0.0))
            if ready_when is not None and ready_when(target_dir):
                # Require the size to hold steady briefly so we don't cut off
                # an install that's still actively writing game data.
                if done == last_done:
                    if stable_since is None:
                        stable_since = now
                    elif now - stable_since >= 2 * poll_interval:
                        ready.set()
                        return
                else:
                    stable_since = None
            last_t, last_done = now, done
            stop.wait(poll_interval)

    watcher = None
    if on_progress is not None or ready_when is not None:
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()

    result = _run_watched(
        runner, cmd, env, repack.path, ready if ready_when is not None else None, stop
    )
    stop.set()
    if watcher is not None:
        watcher.join(timeout=2 * poll_interval)

    if on_progress is not None:
        on_progress(_dir_size(target_dir), estimate, time.monotonic() - started, 0.0)

    # If we stopped early because the game was ready, the installer was
    # terminated on purpose; a non-zero code in that case is expected.
    if not ready.is_set() and result is not None and result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-800:]
        raise InstallError(
            f"Installer exited with code {result.returncode}."
            + (f"\n--- output tail ---\n{tail}" if tail else "")
        )
    return target_dir


def _run_watched(
    runner: Callable[..., subprocess.CompletedProcess],
    cmd: list[str],
    env: dict[str, str],
    cwd: Path,
    ready: threading.Event | None,
    stop: threading.Event,
) -> subprocess.CompletedProcess | None:
    """Run *cmd*. If *ready* fires (game usable), terminate early and return None.

    When *ready* is None this is a plain blocking run via *runner* (keeps tests
    simple and behaviour unchanged for callers that don't use ready_when).
    """
    if ready is None:
        return runner(cmd, env=env, cwd=cwd, capture_output=True, text=True)

    proc = subprocess.Popen(  # noqa: S603 - args are constructed internally
        cmd, env=env, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break  # installer finished on its own
            except subprocess.TimeoutExpired:
                if ready.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return None
    finally:
        stop.set()
    out, err = proc.communicate()
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=out, stderr=err)
