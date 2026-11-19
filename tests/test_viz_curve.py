"""The thinking view's curve ("AI's own estimate") holds the value of the last 20 decisions only, fight plays included.

2026-11-16 (after the first video): the curve over the whole run was too flat to read within a fight; it shows
the last 20 decisions, each card played counts as one, scaled to their own highest and lowest (in the mod). Checked
here on what the bridge writes: never more than 20 points, the newest last (drawn at the right), not cleared when a
fight ends and the next room begins, cleared only when a new run starts (the floor goes down); fewer than 20
decisions give that many points. No simulator or model needed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from spire_bot import panel as P  # noqa: E402


def status(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "STATUS", tmp_path / "spirebot_status.json")
    monkeypatch.setattr(P, "ENABLED", True)
    monkeypatch.setattr(P, "VIZ", True)
    p = P.Panel()
    p.lang = "en"

    def step(floor, value):
        p.think([("Bash", 0.7), ("End turn", 0.3)], 0, 0.7, 2, 3.0, 22077063, value, floor)
        p.show("S_PLAYING", floor, ["x"])
        return json.loads(P.STATUS.read_text(encoding="utf-8"))["viz"]["curve"]
    return step


def test_the_curve_keeps_the_last_20_decisions(tmp_path, monkeypatch):
    step = status(tmp_path, monkeypatch)
    curve = None
    for k in range(5):
        curve = step(3, 0.1 * k)
    assert [v for _, v in curve] == [0.0, 0.1, 0.2, 0.3, 0.4]  # fewer than 20: as many as there are
    for k in range(30):  # a fight on floor 4 (30 plays), then floor 5: one run, nothing cleared
        curve = step(4 if k < 25 else 5, 1 + 0.01 * k)
    assert len(curve) == 20
    assert curve[-1] == [5, 1.29]  # the newest last
    assert curve[0] == [4, 1.1]  # the 20 before it, across the change of floor
    curve = step(0, 0.5)  # a new run
    assert curve == [[0, 0.5]]
