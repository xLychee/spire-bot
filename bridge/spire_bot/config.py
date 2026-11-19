"""Where things are and how the bot plays: spire-bot.toml in the package folder (or the file SPIRE_BOT_SETTINGS names:
the Steam Workshop item's own copy is replaced at each update, so the mod passes the player's copy in ModTheSpire's
config folder when there is one), overridden by CM_* environment variables (used by the tests and by batch runs).

The game starts the bridge with the game's folder as the working directory; saves/ and preferences/ are read there.
"""
from __future__ import annotations

import os
import random
import tomllib
from pathlib import Path

from .seed_string import to_string

ROOT = Path(os.environ.get("SPIRE_BOT_ROOT") or Path(__file__).resolve().parents[2])
GAME_DIR = Path(os.environ.get("SPIRE_GAME_DIR") or Path.cwd())

_file = Path(os.environ.get("SPIRE_BOT_SETTINGS") or ROOT / "spire-bot.toml")
SETTINGS = tomllib.loads(_file.read_text(encoding="utf-8")) if _file.exists() else {}


def _get(env: str, key: str, default):
    """the environment variable if set (even to ""), else the settings file's value, else the default"""
    v = os.environ.get(env)
    return v if v is not None else SETTINGS.get(key, default)


LOG_ROOT = Path(_get("CM_LOG_DIR", "log_dir", ROOT / "logs"))
ADVICE = Path(os.environ.get("CM_ADVICE") or LOG_ROOT / "bot.log")
MODEL = str(_get("CM_MODEL", "model", "model.onnx"))
if not Path(MODEL).is_absolute():
    MODEL = str(ROOT / "models" / MODEL)
ASCENSION = int(_get("CM_ASCENSION", "ascension", 20))
SAVES_DIR = Path(os.environ.get("CM_SAVES_DIR") or GAME_DIR / "saves")
PREFS_DIR = GAME_DIR / "preferences"
PROFILE = Path(os.environ.get("CM_PROFILE") or PREFS_DIR / "STSUnlocks")
PACE = str(_get("CM_PACE", "pace", "bot"))
RESYNC = str(_get("CM_RESYNC", "resync", "all"))
RUBY_HP = float(_get("CM_RUBY_HP", "ruby_hp", 0.6))
PREFLIGHT = str(_get("CM_PREFLIGHT", "preflight", "1")) not in ("0", "false", "False")
CLOSE_GAME = str(_get("CM_CLOSE_GAME", "close_game_when_done", "0")) in ("1", "true", "True")
# the fair search in elite and boss fights: on unless "0"; threads for its batched network calls
SEARCH = str(_get("CM_SEARCH", "search", "1")) not in ("0", "false", "False", "none")
SEARCH_THREADS = int(_get("CM_SEARCH_THREADS", "search_threads", 4))
SEARCH_SCORE = str(_get("CM_SEARCH_SCORE", "search_score", "value"))  # value (the network's estimate) or ledger


def seed_string() -> str:
    """The seed of the run to start: CM_SIM_SEED (a simulator seed number), else the settings file's seed (the
    string the game shows), else a random one."""
    if os.environ.get("CM_SIM_SEED"):
        return to_string(int(os.environ["CM_SIM_SEED"]))
    s = str(SETTINGS.get("seed", "")).strip().upper()
    return s or to_string(random.SystemRandom().randrange(1, 2**40))
