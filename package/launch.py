"""Started by the Spire Bot mod (BridgeProcess.java: the bundled Python, from an argument list), or by CommunicationMod
when its own command points here (an older install). Runs the bridge with the package's own Python and libraries only;
the working directory stays the game's folder.

The libraries are in runtime/site-packages (the zip package, one system) or runtime/win|mac/site-packages (the Steam
Workshop item, both systems)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_OS = "win" if sys.platform == "win32" else "mac"
_SITE = next((p for p in (ROOT / "runtime" / _OS / "site-packages", ROOT / "runtime" / "site-packages") if p.is_dir()),
             ROOT / "runtime" / "site-packages")
sys.path[:0] = [str(ROOT / "bridge"), str(_SITE)]

from spire_bot.bridge import main  # noqa: E402

main()
