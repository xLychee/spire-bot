"""Whole games replayed through the lockstep, from records made with the fake game.

tests/data_synthetic/fake_game_<seed>/ are records of the bridge playing against tests/fake_game.py (the simulator
in the game's part, all relics unlocked, the release model choosing): no real game and no player's profile is in
them. Replayed offline, the lockstep must follow every state to the end without a mismatch or a resync. The two
games between them have a full-belt potion swap, card picks on grids in and out of fights, Nilry's Codex, Liquid
Memories and Gambler's Brew; 4700006 kills the Heart, 4700008 dies on floor 44.

A fake-game record belongs to the simulator build that made it: the simulator plays the game's part in it, so
after a change to the simulator's rules an older record no longer fits (4700008 has a Bronze Automaton fight, and
a record made before the Bronze Orb rules were corrected fails here). These two were recorded with the simulator
of this release (rules of 2026-11-15: the Champ's phase at a resync, Bronze Orbs, Darklings, Writhing Mass's
Parasite, Corruption's costs, costs of cards landing in a pile); re-record them when the simulator changes
(tests/fake_game.py --auto SEED --log-dir DIR --no-lock --pace bot).

Needs the compiled simulator: SPIRE_SIM_SITE=<folder that holds spire_sim>; skipped without it.
  SPIRE_SIM_SITE=... python -m pytest tests/test_fake_record.py
"""
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


@pytest.mark.parametrize("seed,floor", [(4700006, 56), (4700008, 44)])
def test_a_fake_game_replays_in_step(seed, floor):
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    from replay_record import replay
    r = replay(HERE / "data_synthetic" / f"fake_game_{seed}", os.environ["SPIRE_SIM_SITE"])
    assert r["synced"] and r["mismatches"] == []
    assert r["sim_floor"] == floor
    assert [e["event"] for e in r["events"] if e["event"].startswith(("resync_", "rng_drift", "card_rng"))] == []
    assert r["states"] > 500
