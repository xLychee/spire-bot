"""Against the fake game, the time an unmatched state out of fights has stood is counted in frames waited.

A real game gives the lockstep the wall clock: a state out of fights that fits nothing is a miss only after it has
stood for 3 seconds (a card that joins the deck arrives 0.5 to 0.7 s late). Against the fake game a wait comes back
at once, so no real time passes and that rule could never be reached, or would depend on how fast the machine is. The
fake game therefore starts its bridge with CM_FRAME_CLOCK=1, and the bridge counts the frames it has waited (60 a
second) as the lockstep's clock.

The check: a card that joins the deck by anything but a reward pick is shown after 200 frames (3.3 s) instead of 40.
The bridge must take that for a miss out of fights (it stood longer than 3 s) and still play the game to its end
(the card arrives and the state fits again); with the usual 40 frames there is no miss.
A reward pick is left at 40 frames on purpose: the bridge waits for its own reward pick to arrive for at most 180
frames and then sends the pick again, so a reward card later than that would run into that wait first (seed 4500002
stopped on floor 1 that way) and never reach the rule tested here.

Needs the compiled simulator and a model: SPIRE_SIM_SITE=<folder that holds spire_sim>, SPIRE_TEST_MODEL=<an .onnx
model file>; skipped without them. The seed's first such card comes on floor 2 with the model this was written
with (2026-11-16); with another model the game goes another way, and the test then only requires that a miss shows
at 200 frames in some game of the three seeds and none at 40.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEDS = (4500002, 4500010, 4500016)


def play(seed, late_frames):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CM_")}
    env.update(CM_SEARCH="none", CM_KEY_RULE="last", OMP_NUM_THREADS="1", CM_RESYNC="all",
               CM_MODEL=os.environ["SPIRE_TEST_MODEL"],
               PYTHONPATH=f"{HERE.parent / 'bridge'}{os.pathsep}{os.environ['SPIRE_SIM_SITE']}")
    out = Path(tempfile.mkdtemp(prefix="fake_settle_"))
    res = subprocess.run([sys.executable, str(HERE / "fake_game.py"), "--auto", str(seed), "--log-dir", str(out),
                          "--no-lock", "--pace", "bot", "--idle", "20", "--late-frames", str(late_frames)],
                         env=env, cwd=str(out), capture_output=True, text=True).stdout.strip().splitlines()
    r = json.loads(res[-1])
    misses = [json.loads(x) for p in out.rglob("rule_miss.jsonl") for x in open(p)]
    return r, misses


def test_a_card_later_than_three_seconds_is_a_miss_and_one_at_40_frames_is_not():
    import pytest
    if not (os.environ.get("SPIRE_SIM_SITE") and os.environ.get("SPIRE_TEST_MODEL")):
        pytest.skip("SPIRE_SIM_SITE and SPIRE_TEST_MODEL not set")
    late = [play(s, 200) for s in SEEDS]
    assert all(r["finished"] for r, _ in late), [r["bridge_stop"] for r, _ in late]
    out_of_fights = [m for _, ms in late for m in ms if m["where"] == "out of fights"]
    assert out_of_fights, "no miss out of fights although cards arrived after 200 frames"
    usual = [play(s, 40) for s in SEEDS]
    assert all(r["finished"] and r["rule_misses"] == 0 for r, _ in usual), [(r["seed"], r["rule_misses"]) for r, _ in usual]
