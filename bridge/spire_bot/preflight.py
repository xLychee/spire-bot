"""Checks before the bot starts a run. Each problem is one sentence the player can act on; with any problem the bot
does not play."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config as CFG
from .messages import Msg

MARKER = "spirebot_mod.loaded"  # written into the game's folder by the bot's Java mod when the game loads it


def _prefs(name: str) -> dict | None:
    try:
        return json.loads((CFG.PREFS_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def profile_problems() -> list[str]:
    out = []
    if not CFG.PREFS_DIR.is_dir():
        return [Msg("P01", path=CFG.PREFS_DIR)]
    player, data, progress = _prefs("STSPlayer"), _prefs("STSDataVagabond"), _prefs("STSUnlockProgress")
    if player is None or data is None or progress is None:
        return [Msg("P02")]
    wins = [str(player.get(k, "")).lower() == "true" for k in ("IRONCLAD_WIN", "THE_SILENT_WIN", "DEFECT_WIN")]
    if not all(wins):
        out.append(Msg("P03"))
    try:
        asc = int(data.get("ASCENSION_LEVEL", 0))
    except ValueError:
        asc = 0
    if asc < CFG.ASCENSION:
        out.append(Msg("P04", want=CFG.ASCENSION, have=asc))
    try:
        level = int(progress.get("IRONCLADUnlockLevel", 0))
    except ValueError:
        level = 0
    if level < 5:
        out.append(Msg("P05", level=level))
    return out


def run_in_progress() -> bool:
    """An unfinished Ironclad run: starting a new run would overwrite it."""
    return (CFG.SAVES_DIR / "IRONCLAD.autosave").exists()


def mod_loaded(bridge_started: float) -> bool:
    """Did this game load the bot's Java mod? The mod writes '<pid> <unix seconds>' into the marker file."""
    try:
        pid, t = (CFG.GAME_DIR / MARKER).read_text().split()[:2]
    except (OSError, ValueError):
        return False
    return int(pid) == os.getppid() or float(t) >= bridge_started - 300
