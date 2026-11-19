"""The fair search in elite and boss fights: bridge/spire_bot/search.py.

- batch_many: the copies' features as one batch give the same scores as one at a time (a model exported with a
  variable batch size; tools/export_onnx.py);
- the search runs only in elite and boss fights, on a decision the network is unsure of, and picks one of the
  network's best options; the same seed gives the same pick (its copies' random stream is the run's);
- the ledger: the development project's numbers (search.ledger_score);
- the setting: on in the package's spire-bot.toml, off with "0".
The parity with the development FairSearch(score="ledger") on the same states is checked by a developer script, not here: it needs torch.

Needs the compiled simulator with determinize (a release core with search support) and a model:
SPIRE_SIM_SITE, SPIRE_TEST_MODEL; skipped without them (the ledger and setting tests always run).
"""
import os
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bridge"))
if os.environ.get("SPIRE_SIM_SITE"):
    sys.path.insert(0, os.environ["SPIRE_SIM_SITE"])
from spire_bot import search as S  # noqa: E402


def test_ledger_prices_as_in_development():
    st = {"act": 2, "hp": 40, "max_hp": 80, "gold": 150, "potions": [[0, "Block Potion"], [1, "EMPTY_POTION_SLOT"]]}
    assert S.ledger_score(st) == pytest.approx(0.25 * 0.5 + 0.029 * 1 + 0.065 * 1.5 + 0.007 * 80)
    st4 = dict(st, act=4, gold=999)
    assert S.ledger_score(st4) == pytest.approx(1.8 * 0.5 + 0.11 + 0.0 + 0.018 * 80)  # gold is worth nothing in act 4


def test_on_by_default_in_the_package():
    t = tomllib.loads((HERE.parent / "package" / "spire-bot.toml").read_text())
    assert t["search"] == "1"


def needs():
    if not (os.environ.get("SPIRE_SIM_SITE") and os.environ.get("SPIRE_TEST_MODEL")):
        pytest.skip("SPIRE_SIM_SITE and SPIRE_TEST_MODEL not set")
    import spire_sim
    if not hasattr(spire_sim._core, "determinize"):
        pytest.skip("this simulator build has no determinize (a release core search)")
    import onnxruntime as ort
    sess = ort.InferenceSession(os.environ["SPIRE_TEST_MODEL"], providers=["CPUExecutionProvider"])
    if isinstance(sess.get_inputs()[0].shape[0], int):
        pytest.skip("the model's batch size is fixed")
    return spire_sim, sess, [i.name for i in sess.get_inputs()]


def to_elite_decision(spire_sim, sess, inputs, seed=4500001, conf=0.9):
    """plays the network from the run's start to its first elite or boss fight decision below conf"""
    from spire_bot.policy import batch_of, best
    e = spire_sim.Env(seed, 20)
    while not e.done:
        f = e.features()
        b = batch_of(f)
        lg = sess.run(["logits"], {k: b[k] for k in inputs})[0][0]
        c = best(lg)
        if str(e.space).endswith("BATTLE") and e.state().get("room") in S.ROOMS:
            p = np.exp(lg - lg.max()); p /= p.sum()
            if p[c] < conf and (p > 0.01).sum() >= 2:
                return e, f, lg, c
        e.step(int(f["actions"][c]))
    pytest.skip("no unsure elite or boss decision in this run")


def test_batched_scores_equal_one_at_a_time():
    spire_sim, sess, inputs = needs()
    from spire_bot.policy import batch_of
    e = spire_sim.Env(4500002, 20)
    fs = []
    for _ in range(40):
        f = e.features()
        fs.append(f)
        e.step(int(f["actions"][0]))
    b = S.batch_many(fs)
    many = sess.run(["logits"], {k: b[k] for k in inputs})[0]
    for i, f in enumerate(fs):
        one = sess.run(["logits"], {k: v for k, v in batch_of(f).items() if k in inputs})[0][0]
        assert np.allclose(many[i, : len(one)], one, atol=1e-4), i
        assert np.isneginf(many[i, len(one):]).all()


def test_search_in_an_elite_fight_picks_a_top_option_and_repeats():
    spire_sim, sess, inputs = needs()
    e, f, lg, c = to_elite_decision(spire_sim, sess, inputs)
    picks = []
    for _ in range(2):
        fs = S.FairSearch(sess, inputs, True, n=4, seed=7)
        assert fs.wants(e)
        picks.append(fs.choose(e, f, lg, c))
        assert fs.last is not None and fs.last["copies"] == 4 * len(fs.last["options"])
        assert picks[-1] in fs.last["options"] and c in fs.last["options"]
    assert picks[0] == picks[1]
    one = S.FairSearch(sess, inputs, False, n=4, seed=7)     # one at a time: the same pick
    assert one.choose(e, f, lg, c) == picks[0]


def test_no_search_when_sure_or_out_of_elite_and_boss():
    spire_sim, sess, inputs = needs()
    e, f, lg, c = to_elite_decision(spire_sim, sess, inputs)
    fs = S.FairSearch(sess, inputs, True, n=4, conf=0.0)       # conf 0: always sure enough
    assert fs.choose(e, f, lg, c) == c and fs.last is None
    first = spire_sim.Env(4500001, 20)
    while not str(first.space).endswith("BATTLE"):
        first.step(int(first.features()["actions"][0]))
    assert first.state().get("room") not in S.ROOMS and not fs.wants(first)


def test_the_search_record_is_for_this_decision_only():
    """the bridge logs Policy.search.last after each decision: a decision without a search must leave none (seen in
    a fake game: one elite search's record logged again at every later step, shops and events included)"""
    spire_sim, sess, inputs = needs()
    from spire_bot.policy import Policy
    pol = Policy(os.environ["SPIRE_TEST_MODEL"], search=True, search_threads=1)
    e, f, lg, c = to_elite_decision(spire_sim, sess, inputs)
    pol.decide(e, 4500001)
    assert pol.search is not None and pol.search.last is not None
    e2 = spire_sim.Env(4500001, 20)                 # the run's first step: Neow, no fight
    pol.decide(e2, 4500001)
    assert pol.search.last is None


def test_value_score_is_the_default_and_ledger_an_option():
    """The release scores a copy by the network's value after the fight; the ledger stays as an option (spire-bot.toml search_score = "ledger")"""
    t = tomllib.loads((HERE.parent / "package" / "spire-bot.toml").read_text())
    assert t.get("search_score", "value") == "value"
    spire_sim, sess, inputs = needs()
    e, f, lg, c = to_elite_decision(spire_sim, sess, inputs)
    v = S.FairSearch(sess, inputs, True, n=4, seed=7)
    assert v.score == "value"
    pv = v.choose(e, f, lg, c)
    assert pv in v.last["options"]
    ld = S.FairSearch(sess, inputs, True, n=4, seed=7, score="ledger")
    ld.choose(e, f, lg, c)
    # the same copies, other scores: value scores are the network's estimate (about -1..5), not the ledger's
    assert v.last["scores"] != ld.last["scores"]
    assert v.last["score"] == "value" and ld.last["score"] == "ledger"
