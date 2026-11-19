"""A fight that cannot be rebuilt from the game's state does not leave the lockstep "in step".

When the simulator refuses the game's state in a rebuild (set_battle_from_cm raises), the error used to pass up to
the bridge's main loop, which logged it and went on; the lockstep's "in step" flag stayed as it was, and the bot
played its next card from the state before the miss. In the fake game's stress test (every redrawing choice lasts
120 frames, so no search finds the count and the fight has to be rebuilt; the fake game's fight state is too thin
to rebuild from) three of 25 games stopped that way: "card ... not playable in the game hand". No real game has
shown this (530 records, no such error).

Now the error is caught at the rebuild: it is logged, the floor's autosave is tried (the usual fallback), and if
that does not fit either the lockstep is out of step, so the bot waits and then stops for the right reason.

The record is the fake game of seed 4500011 in that stress test, as the old bridge played it (it stopped on
floor 33). No real game is in it. The stress option that made it is not in this tree (a development branch): the
test only replays the record. Needs the compiled simulator: SPIRE_SIM_SITE=<folder that holds spire_sim>;
skipped without it.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RECORD = HERE / "data_synthetic" / "fake_stress_4500011"


def test_a_rebuild_that_raises_ends_out_of_step_with_the_error_logged():
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    from replay_record import open_record
    B, lock, recs, feed = open_record(RECORD, os.environ["SPIRE_SIM_SITE"])
    raised = []
    for rec in recs:
        try:
            feed(rec)
        except Exception as ex:  # (the old bridge: the error leaves the lockstep; its main loop caught it there)
            raised.append(repr(ex))
    assert not raised, f"{len(raised)} states raised out of the lockstep: {raised[0]}"
    assert not lock.synced, "the lockstep counts as in step after a rebuild that raised"
    events = [json.loads(x) for x in open(B.LOG_DIR / "events.jsonl")]
    failed = [e for e in events if e["event"] == "resync_from_game" and not e["ok"] and e.get("error")]
    assert failed and failed[0]["floor"] == 33 and "current_hp" in failed[0]["error"], events[-3:]
    errors = [json.loads(x) for x in open(B.LOG_DIR / "errors.jsonl")]
    assert errors and "current_hp" in errors[0]["error"]
