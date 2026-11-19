"""The panel's thinking view shows the decision the bot actually takes, from the scores it took it from.

Against the fake game (tests/fake_game.py, the simulator in the game's part, --panel-trace keeps the status file the
panel would read after every command), at every command that follows a new decision:
- the option the view marks as chosen is the command the bridge sends (the one exception: a full-belt potion swap,
  where the bridge first sends the discard and the chosen pick comes next);
- its share is the highest among the rows (the tie rule and the key rule pick among the scores shown, so nothing
  can be chosen over a larger share), and the rows' shares are in order, between 0 and 1, summing to at most 1;
- with a model file that has the value head, the curve's last point is this step's value, and it has at most 20
  points (the last 20 decisions).

Needs the compiled simulator and a model: SPIRE_SIM_SITE=<folder that holds spire_sim>, SPIRE_TEST_MODEL=<an .onnx
model file>; skipped without them.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 4500001


def trace(seed):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CM_")}
    env.update(CM_SEARCH="none", CM_KEY_RULE="last", OMP_NUM_THREADS="1", CM_RESYNC="all",
               CM_MODEL=os.environ["SPIRE_TEST_MODEL"],
               PYTHONPATH=f"{HERE.parent / 'bridge'}{os.pathsep}{os.environ['SPIRE_SIM_SITE']}")
    out = Path(tempfile.mkdtemp(prefix="viz_status_"))
    res = subprocess.run([sys.executable, str(HERE / "fake_game.py"), "--auto", str(seed), "--log-dir", str(out),
                          "--no-lock", "--pace", "bot", "--idle", "20", "--panel-trace"],
                         env=env, cwd=str(out), capture_output=True, text=True).stdout.strip().splitlines()
    return json.loads(res[-1]), json.loads((out / "panel_trace.json").read_text())


def test_the_view_marks_the_command_sent_and_its_scores():
    import pytest
    if not (os.environ.get("SPIRE_SIM_SITE") and os.environ.get("SPIRE_TEST_MODEL")):
        pytest.skip("SPIRE_SIM_SITE and SPIRE_TEST_MODEL not set")
    r, tr = trace(SEED)
    assert r["finished"], r.get("bridge_stop")
    seen, checked, wrong, bad_rows, bad_curve, valued = None, 0, [], [], [], 0
    for k, t in enumerate(tr):
        v = t.get("viz")
        if t.get("n") == seen or t["cmd"].split()[0] in ("state", "wait") or not v:
            continue  # (no new decision: the status was written for something else, or not at all)
        seen = t.get("n")
        checked += 1
        sent = t["cmd"]
        if sent != v["cmd"] and not (sent.startswith("potion discard") and k + 1 < len(tr) and tr[k + 1]["cmd"] == v["cmd"]):
            wrong.append((k, sent, v["cmd"], v["rows"][v["chosen"]][0]))
        q = [x[1] for x in v["rows"]]
        if not (all(0 <= x <= 1 for x in q) and sum(q) <= 1 + 1e-3 and q[v["chosen"]] >= max(q) - 1e-3
                and all(q[i] >= q[i + 1] - 1e-3 for i in range(len(q) - 1) if v["chosen"] not in (i, i + 1))):
            bad_rows.append((k, sent, v["rows"], v["chosen"]))
        if v.get("value") is not None:
            valued += 1
            if not v["curve"] or v["curve"][-1][1] != v["value"] or len(v["curve"]) > 20:
                bad_curve.append((k, v["curve"][-3:] if v["curve"] else None, v["value"]))
    assert checked > 300
    assert wrong == [], wrong[:5]
    assert bad_rows == [], bad_rows[:5]
    assert bad_curve == [], bad_curve[:5]
    print(f"{checked} decisions checked, {valued} with a value")
