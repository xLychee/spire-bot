"""The fake game lists the cards already picked on a several-card grid, as the real game does.

On a grid that asks for several cards out of fights (Astrolabe's three, a removal or transform of two) the real game
shows the cards picked so far under selected_cards while the deck stays as it is until the last pick; the lockstep
reads its picks so far from there. The fake game left that list empty, so after a pick the branch that had not
picked fitted as well and stayed first, and the bot sent the same pick again: "card ... not on the grid" (the fake
game of seed 4600026 with the development bridge, Astrolabe on floor 34).

Here a several-card grid is found at a run's first choice (Neow), one card is picked, and the fake game's state must
say so in the way the bridge reads it. Needs the compiled simulator: SPIRE_SIM_SITE=<folder that holds spire_sim>;
skipped without it.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_a_pick_on_a_several_card_grid_shows_as_selected():
    import pytest
    if not os.environ.get("SPIRE_SIM_SITE"):
        pytest.skip("SPIRE_SIM_SITE not set")
    sys.path[:0] = [str(HERE), str(HERE.parent / "bridge"), os.environ["SPIRE_SIM_SITE"]]
    from spire_sim import Env
    import fake_game
    from spire_bot import bridge as B
    found = None
    for seed in range(4700001, 4700400):
        e = Env(seed, 20)
        for a in e.legal_actions():
            c = e.clone()
            c.step(a)
            if c.done or c.screen != "card_select":
                continue
            c.step(c.legal_actions()[0])
            if not c.done and c.screen == "card_select" and c.state().get("select_picked"):
                found = (seed, c)
                break
        if found:
            break
    assert found, "no several-card grid at a first choice in 400 seeds"
    seed, env = found
    g = fake_game.FakeGame(20)
    g.env, g.seed = env, seed
    gs = g.state()["game_state"]
    ss = gs["screen_state"]
    assert gs["screen_type"] == "GRID"
    picked = [name + ("+" if up else "") for up, name in env.state()["select_picked"]]
    assert [c["name"] for c in ss["selected_cards"]] == picked and len(picked) == 1
    assert ss["num_cards"] > len(picked)
    # and the bridge reads it: the picks so far are part of what a simulator branch must match
    assert B.cm_signature(gs).get("grid_picked") == sorted(B._norm(p) for p in picked)
