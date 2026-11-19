"""Read the game's autosave (saves/<CLASS>.autosave in the game's folder): base64, then XOR with the bytes of "key".
The game writes it each time a room is entered. The caller gives the saves folder (config.SAVES_DIR)."""
from __future__ import annotations

import base64
import json
from pathlib import Path


def decode(raw: bytes) -> dict:
    data = base64.b64decode(raw)
    key = b"key"
    return json.loads(bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode("utf-8"))


def load(saves: Path, cls: str = "IRONCLAD") -> dict | None:
    p = Path(saves) / f"{cls}.autosave"
    return decode(p.read_bytes()) if p.exists() else None
