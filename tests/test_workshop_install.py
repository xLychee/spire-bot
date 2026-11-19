"""The mod starts the bridge itself (BridgeProcess.java); the zip package's install only tells it where the
package is, and leaves CommunicationMod's command alone unless an older install of this bot wrote it.

Also: launch.py takes the libraries of its own system in the Steam Workshop item (runtime/win|mac/site-packages), and
config.py reads the settings file SPIRE_BOT_SETTINGS names (the player's copy, kept across Workshop updates)."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "package"


@pytest.fixture
def inst(tmp_path, monkeypatch):
    """install.py with ModTheSpire's config folder in tmp_path, and a package root with the mod's jar"""
    sys.path.insert(0, str(PKG))
    import install
    importlib.reload(install)
    sys.path.remove(str(PKG))
    cfg = tmp_path / "mts"
    monkeypatch.setattr(install, "mts_config_dir", lambda: cfg)
    root = tmp_path / "unzipped here" / "spire-bot"  # a path with spaces: fine now
    (root / "mod").mkdir(parents=True)
    (root / "mod" / "spirebot.jar").write_bytes(b"jar")
    (root / "models").mkdir()
    monkeypatch.setattr(install, "ROOT", root)
    game = tmp_path / "game"
    game.mkdir()
    (game / "desktop-1.0.jar").write_bytes(b"")
    return install, cfg, root, game


def read_props(p: Path) -> dict:
    out = {}
    for ln in p.read_text(encoding="latin-1").splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v
    return out


def test_install_writes_bridge_dir_not_the_command(inst):
    install, cfg, root, game = inst
    install.install(game)
    assert (game / "mods" / "spirebot.jar").read_bytes() == b"jar"
    bd = read_props(cfg / "spirebot" / "config.properties")["bridge_dir"]
    # forward slashes and an escaped drive colon read back by java.util.Properties as the folder
    assert bd.replace("\\:", ":") == str(root).replace("\\", "/")
    assert not (cfg / "CommunicationMod" / "config.properties").exists()  # untouched (there was none)


def test_install_leaves_another_programs_command(inst, capsys):
    install, cfg, root, game = inst
    cm = cfg / "CommunicationMod" / "config.properties"
    cm.parent.mkdir(parents=True)
    cm.write_text("command=python my_own_bot.py\nrunAtGameStart=true\n", encoding="latin-1")
    install.install(game)
    assert cm.read_text(encoding="latin-1") == "command=python my_own_bot.py\nrunAtGameStart=true\n"
    assert "Spire Bot does not start while that is set" in capsys.readouterr().out


def test_install_takes_out_an_older_installs_command(inst):
    install, cfg, root, game = inst
    cm = cfg / "CommunicationMod" / "config.properties"
    cm.parent.mkdir(parents=True)
    cm.write_text("command=C:/spire-bot/runtime/python/python.exe -I C:/spire-bot/launch.py\nrunAtGameStart=true\n",
                  encoding="latin-1")
    (cm.parent / install.BACKUP).write_text("verbose=true\n", encoding="latin-1")
    install.install(game)
    assert cm.read_text(encoding="latin-1") == "verbose=true\n"  # the player's settings from before
    assert not (cm.parent / install.BACKUP).exists()


def test_uninstall_removes_the_jar_and_bridge_dir(inst):
    install, cfg, root, game = inst
    install.install(game)
    install.uninstall(game)
    assert not (game / "mods" / "spirebot.jar").exists()
    assert not (cfg / "spirebot" / "config.properties").exists()


def test_launch_takes_its_systems_libraries(tmp_path):
    root = tmp_path / "spire-bot"
    osn = "win" if sys.platform == "win32" else "mac"
    site = root / "runtime" / osn / "site-packages"
    (site / "spire_bot").mkdir(parents=True)
    (root / "runtime" / ("mac" if osn == "win" else "win") / "site-packages").mkdir(parents=True)
    (site / "spire_bot" / "__init__.py").write_text("")
    (site / "spire_bot" / "bridge.py").write_text("import sys\ndef main():\n    print(sys.path[1])\n")
    (root / "launch.py").write_bytes((PKG / "launch.py").read_bytes())
    out = subprocess.run([sys.executable, "-I", str(root / "launch.py")], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(site)


def test_settings_file_from_the_environment(tmp_path):
    s = tmp_path / "my settings.toml"
    s.write_text('ascension = 17\nsearch = "0"\n', encoding="utf-8")
    code = ("import sys; sys.path.insert(0, %r); from spire_bot import config as C; print(C.ASCENSION, C.SEARCH)"
            % str(HERE.parent / "bridge"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("CM_")}
    env["SPIRE_BOT_SETTINGS"] = str(s)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
    assert out.stdout.split() == ["17", "False"]
