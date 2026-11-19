"""Installs or removes the bot in your copy of Slay the Spire.

  install:    python install.py [--game GAME_FOLDER]
  uninstall:  python install.py --uninstall [--game GAME_FOLDER]

Install does two things, and uninstall undoes them:
  1. copies mod/spirebot.jar into the game's mods folder;
  2. tells the mod where this package is (bridge_dir in ModTheSpire's config folder, spirebot/config.properties); the
     mod starts the bot itself. CommunicationMod's own settings are left as they are; only a command an older install
     of this bot wrote there is taken out again (its settings from before are put back).
Nothing else on the computer is changed. GAME_FOLDER is the folder that holds desktop-1.0.jar.
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = "config.properties.before-spirebot"


def steam_libraries():
    homes = []
    if sys.platform == "win32":
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            homes.append(Path(winreg.QueryValueEx(k, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
        homes.append(Path(r"C:\Program Files (x86)\Steam"))
    elif sys.platform == "darwin":
        homes.append(Path.home() / "Library/Application Support/Steam")
    else:
        homes += [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"]
    libs = []
    for h in homes:
        libs.append(h)
        vdf = h / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def find_game():
    for lib in steam_libraries():
        g = lib / "steamapps" / "common" / "SlayTheSpire"
        for cand in (g, g / "SlayTheSpire.app" / "Contents" / "Resources"):
            if (cand / "desktop-1.0.jar").exists():
                return cand
    return None


def mts_config_dir() -> Path:
    """ModTheSpire's config folder (its SpireConfig files live there, one folder per mod)"""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ModTheSpire"
    if sys.platform == "darwin":
        return Path.home() / "Library/Preferences/ModTheSpire"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ModTheSpire"


def cm_config() -> Path:
    return mts_config_dir() / "CommunicationMod" / "config.properties"


def bot_config() -> Path:
    """this mod's own settings in ModTheSpire's config: bridge_dir, where this package is (the mod reads it)"""
    return mts_config_dir() / "spirebot" / "config.properties"


def _prop(v: str) -> str:
    """a value as a .properties file needs it (Java reads backslashes as escapes and the file as Latin-1)"""
    out = []
    for ch in v.replace("\\", "/"):
        if ch in "\\:=#!":
            out.append("\\" + ch)
        elif ord(ch) > 126:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _ours(command: str) -> bool:
    """a CommunicationMod command written by an older install of this bot"""
    return "launch.py" in command and ("spire-bot" in command or "spire_bot" in command)


def _cm_command() -> str:
    cfg = cm_config()
    if not cfg.exists():
        return ""
    for ln in cfg.read_text(encoding="latin-1").splitlines():
        m = re.match(r"\s*command\s*[=:]\s*(.*)$", ln)
        if m:
            return m.group(1).strip()
    return ""



def install(game: Path):
    jar = ROOT / "mod" / "spirebot.jar"
    (game / "mods").mkdir(exist_ok=True)
    shutil.copyfile(jar, game / "mods" / "spirebot.jar")
    print("copied", game / "mods" / "spirebot.jar")
    bc = bot_config()
    bc.parent.mkdir(parents=True, exist_ok=True)
    bc.write_text(f"bridge_dir={_prop(str(ROOT))}\n", encoding="latin-1")
    print("the mod starts the bot from", ROOT, f"(written to {bc})")
    _undo_old_command()
    other = _cm_command()
    if other:
        print(f"\nCommunication Mod is set to start another program:\n  {other}\nSpire Bot does not start while that is "
              "set (two programs would both send commands to the game). To let Spire Bot play, clear the command in "
              f"Communication Mod's settings ({cm_config()}).")
    if not any((ROOT / "models").glob("*.onnx")):
        print("\nNo model file yet: download one and put it into", ROOT / "models", "(see README).")
    print("\nDone. Start the game with mods (BaseMod, CommunicationMod and Spire Bot ticked). The bot starts a run by "
          "itself from the main menu; logs/bot.log says what it is doing.")


def _undo_old_command():
    """An older install pointed CommunicationMod's command at this bot: put the player's settings back as they were
    before it (the copy it kept), or clear that command."""
    cfg = cm_config()
    if not cfg.exists() or not _ours(_cm_command()):
        return
    b = cfg.parent / BACKUP
    if b.exists():
        shutil.copyfile(b, cfg)
        b.unlink()
        print("put back Communication Mod's settings from before the older install:", cfg)
    else:
        keep = [ln for ln in cfg.read_text(encoding="latin-1").splitlines()
                if not re.match(r"\s*(command|runAtGameStart)\s*[=:]", ln)]
        cfg.write_text("\n".join(keep + ["command="]) + "\n", encoding="latin-1")
        print("cleared the older install's command in", cfg)


def uninstall(game: Path):
    j = game / "mods" / "spirebot.jar"
    if j.exists():
        j.unlink()
        print("removed", j)
    for name in ("spirebot_status.json", "spirebot_status.tmp", "spirebot_control.json", "spirebot_mod.loaded"):
        if (game / name).exists():
            (game / name).unlink()
    bc = bot_config()
    if bc.exists():
        bc.unlink()
        print("removed", bc)
    _undo_old_command()
    print("Done. You can delete the spire-bot folder.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", help="the game's folder (the one with desktop-1.0.jar)")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    game = Path(a.game) if a.game else find_game()
    if game is None or not (game / "desktop-1.0.jar").exists():
        sys.exit("Slay the Spire was not found. Run again with --game followed by the game's folder (the one that "
                 "holds desktop-1.0.jar; in Steam: right-click the game, Manage, Browse local files).")
    print("game:", game)
    (uninstall if a.uninstall else install)(game)


if __name__ == "__main__":
    main()
