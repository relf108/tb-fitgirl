import hashlib
import os
import subprocess
import sys
import threading
import time

import pytest

from tb_fitgirl.installer import (
    InstallError,
    _run_watched,
    estimate_installed_size,
    find_repack,
    install,
    verify_bins,
)


@pytest.fixture
def repack_dir(tmp_path):
    d = tmp_path / "DELTARUNE [FitGirl Repack]"
    (d / "MD5").mkdir(parents=True)
    (d / "setup.exe").write_bytes(b"MZ fake installer")
    (d / "fg-01.bin").write_bytes(b"archive one")
    (d / "fg-02.bin").write_bytes(b"archive two")
    (d / "fg-optional-bonus-soundtrack.bin").write_bytes(b"music")
    lines = []
    for name in ("fg-01.bin", "fg-02.bin", "fg-optional-bonus-soundtrack.bin", "setup.exe"):
        md5 = hashlib.md5((d / name).read_bytes()).hexdigest()
        lines.append(f"{md5} *..\\{name}")  # real files use windows paths relative to MD5/
    (d / "MD5" / "fitgirl-bins.md5").write_text("; FitGirl checksums\n" + "\n".join(lines) + "\n")
    return d


def test_find_repack(repack_dir):
    repack = find_repack(repack_dir)
    assert repack.game_name == "DELTARUNE"
    assert [b.name for b in repack.bins] == ["fg-01.bin", "fg-02.bin"]
    assert [b.name for b in repack.optional_bins] == ["fg-optional-bonus-soundtrack.bin"]
    assert repack.md5_file is not None


def test_find_repack_rejects_non_repack(tmp_path):
    with pytest.raises(InstallError, match="No setup.exe"):
        find_repack(tmp_path)
    (tmp_path / "setup.exe").write_bytes(b"MZ")
    with pytest.raises(InstallError, match="no fg-"):
        find_repack(tmp_path)


def test_verify_bins_ok(repack_dir):
    assert verify_bins(find_repack(repack_dir)) == []


def test_verify_bins_detects_corruption(repack_dir):
    (repack_dir / "fg-02.bin").write_bytes(b"corrupted!")
    (repack_dir / "fg-01.bin").unlink()
    failures = verify_bins(find_repack(repack_dir))
    assert "..\\fg-01.bin: missing" in failures
    assert "..\\fg-02.bin: checksum mismatch" in failures


def test_verify_bins_rejects_escaping_paths(repack_dir):
    md5 = repack_dir / "MD5" / "fitgirl-bins.md5"
    md5.write_text("d41d8cd98f00b204e9800998ecf8427e *..\\..\\..\\etc\\passwd\n")
    failures = verify_bins(find_repack(repack_dir))
    assert failures == ["..\\..\\..\\etc\\passwd: outside repack directory"]


def test_install_builds_wine_command(repack_dir, tmp_path):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    target = tmp_path / "lib" / "DELTARUNE"
    repack = find_repack(repack_dir)
    result = install(repack, target, wine_prefix=tmp_path / "prefix", runner=fake_run)

    assert result == target
    assert target.is_dir()
    cmd = calls["cmd"]
    assert cmd[0] == "wine"
    assert cmd[1].endswith("setup.exe")
    assert "/SILENT" in cmd
    assert "/VERYSILENT" not in cmd  # keeps progress window for ISDone's pump
    assert any(a.startswith("/DIR=Z:\\") for a in cmd)
    assert calls["env"]["WINEPREFIX"] == str(tmp_path / "prefix")


def test_install_gui_mode_omits_silent_flags(repack_dir, tmp_path):
    def fake_run(cmd, **kwargs):
        assert "/SILENT" not in cmd and "/VERYSILENT" not in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(find_repack(repack_dir), tmp_path / "t", silent=False, runner=fake_run)


def test_install_failure_raises_with_output(repack_dir, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="wine: something broke")

    with pytest.raises(InstallError, match="something broke"):
        install(find_repack(repack_dir), tmp_path / "t", runner=fake_run)


def _unused_runner(*a, **k):  # pragma: no cover - never called on the ready path
    raise AssertionError("runner should not be called when ready is set")


def test_run_watched_terminates_when_ready(tmp_path):
    """_run_watched kills a still-running installer once ready fires."""
    ready = threading.Event()
    stop = threading.Event()
    cmd = [sys.executable, "-c", "import time; time.sleep(20)"]

    def fire_ready():
        time.sleep(0.3)
        ready.set()

    threading.Thread(target=fire_ready, daemon=True).start()
    start = time.monotonic()
    result = _run_watched(_unused_runner, cmd, dict(os.environ), tmp_path, ready, stop)
    elapsed = time.monotonic() - start

    assert result is None  # terminated early, not a normal completion
    assert elapsed < 15  # did not wait the full 20s
    assert stop.is_set()


def test_run_watched_normal_completion(tmp_path):
    """Without ready firing, _run_watched returns the process result."""
    cmd = [sys.executable, "-c", "print('done')"]
    result = _run_watched(
        _unused_runner, cmd, dict(os.environ), tmp_path, threading.Event(), threading.Event()
    )
    assert result is not None
    assert result.returncode == 0


def test_run_watched_no_ready_uses_runner(tmp_path):
    """When ready is None, _run_watched delegates to the injected runner."""
    called = {}

    def fake_runner(cmd, **kwargs):
        called["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    result = _run_watched(fake_runner, ["x"], {}, tmp_path, None, threading.Event())
    assert called["cmd"] == ["x"]
    assert result is not None
    assert result.returncode == 0


def test_estimate_installed_size(repack_dir):
    repack = find_repack(repack_dir)
    assert estimate_installed_size(repack) == repack.archive_bytes * 2


def test_install_reports_progress(repack_dir, tmp_path):
    target = tmp_path / "game"

    def fake_run(cmd, **kwargs):
        # Simulate the installer writing a file while the poller runs.
        target.mkdir(parents=True, exist_ok=True)
        (target / "game.dat").write_bytes(b"x" * 4096)
        time.sleep(0.05)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    calls = []
    install(
        find_repack(repack_dir),
        target,
        runner=fake_run,
        on_progress=lambda done, total, elapsed, rate: calls.append((done, total, elapsed, rate)),
        poll_interval=0.01,
    )
    # Final callback always fires and should reflect the written file.
    assert calls
    final_done, final_total, _, final_rate = calls[-1]
    assert final_done >= 4096
    assert final_total == find_repack(repack_dir).archive_bytes * 2
    assert final_rate >= 0


def test_install_mutes_wine_audio_by_default(repack_dir, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(find_repack(repack_dir), tmp_path / "t", wine_prefix=tmp_path / "p", runner=fake_run)
    assert "winepulse.drv=d" in captured["env"]["WINEDLLOVERRIDES"]


def _fake_steam(tmp_path, monkeypatch, *, with_runtime=False):
    from tb_fitgirl import steam

    root = tmp_path / "Steam"
    (root / "steamapps" / "common").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(f'"a"{{"path" "{root}"}}')
    monkeypatch.setattr(steam, "steam_root", lambda: root)

    proton_dir = root / "steamapps" / "common" / "Proton"
    proton_dir.mkdir()
    proton = proton_dir / "proton"
    proton.write_text("#!/usr/bin/env python3\n")
    if with_runtime:
        (proton_dir / "toolmanifest.vdf").write_text('"manifest"{"require_tool_appid" "4183110"}')
        runtime = root / "steamapps" / "common" / "SteamLinuxRuntime_4"
        runtime.mkdir()
        (runtime / "_v2-entry-point").write_text("#!/bin/sh\n")
    return root, proton


def test_install_via_proton_builds_command(repack_dir, tmp_path, monkeypatch):
    root, proton = _fake_steam(tmp_path, monkeypatch)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(
        find_repack(repack_dir),
        tmp_path / "game",
        runtime="proton",
        proton=proton,
        use_steam_run=False,
        wine_prefix=tmp_path / "compat",
        runner=fake_run,
    )
    cmd = captured["cmd"]
    assert cmd[0] == str(proton) and cmd[1] == "waitforexitandrun"
    assert cmd[2].endswith("setup.exe")
    assert "/SILENT" in cmd
    env = captured["env"]
    assert env["STEAM_COMPAT_DATA_PATH"] == str(tmp_path / "compat")
    assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(root)


def test_install_proton_wraps_in_runtime_container(repack_dir, tmp_path, monkeypatch):
    root, proton = _fake_steam(tmp_path, monkeypatch, with_runtime=True)
    entry = root / "steamapps" / "common" / "SteamLinuxRuntime_4" / "_v2-entry-point"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(
        find_repack(repack_dir),
        tmp_path / "game",
        runtime="proton",
        proton=proton,
        use_steam_run=False,
        wine_prefix=tmp_path / "compat",
        runner=fake_run,
    )
    cmd = captured["cmd"]
    assert cmd[0] == str(entry)
    assert cmd[1] == "--verb=run"
    assert cmd[2] == "--"
    assert cmd[3] == str(proton) and cmd[4] == "waitforexitandrun"


def test_install_proton_wraps_in_steam_run(repack_dir, tmp_path, monkeypatch):
    import tb_fitgirl.installer as inst

    root, proton = _fake_steam(tmp_path, monkeypatch)
    monkeypatch.setattr(inst, "_steam_run_wrapper", lambda: "/usr/bin/steam-run")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(
        find_repack(repack_dir),
        tmp_path / "game",
        runtime="proton",
        proton=proton,
        wine_prefix=tmp_path / "compat",
        runner=fake_run,
    )
    assert captured["cmd"][0] == "/usr/bin/steam-run"


def test_install_proton_steam_run_opt_out(repack_dir, tmp_path, monkeypatch):
    import tb_fitgirl.installer as inst

    root, proton = _fake_steam(tmp_path, monkeypatch)
    monkeypatch.setattr(inst, "_steam_run_wrapper", lambda: "/usr/bin/steam-run")

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(
        find_repack(repack_dir),
        tmp_path / "game",
        runtime="proton",
        proton=proton,
        use_steam_run=False,
        wine_prefix=tmp_path / "compat",
        runner=fake_run,
    )
    assert captured["cmd"][0] != "/usr/bin/steam-run"


def test_install_proton_requires_path(repack_dir, tmp_path):
    with pytest.raises(InstallError, match="requires a proton script"):
        install(find_repack(repack_dir), tmp_path / "g", runtime="proton")


def test_install_unknown_runtime(repack_dir, tmp_path):
    with pytest.raises(InstallError, match="Unknown runtime"):
        install(find_repack(repack_dir), tmp_path / "g", runtime="dosbox")


def test_install_mute_can_be_disabled(repack_dir, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    install(
        find_repack(repack_dir),
        tmp_path / "t",
        wine_prefix=tmp_path / "p",
        mute=False,
        runner=fake_run,
    )
    assert "winepulse.drv=d" not in captured["env"].get("WINEDLLOVERRIDES", "")
