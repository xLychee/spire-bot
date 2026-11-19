"""Fair search in elite and boss fights: the development project's fair search, on the release simulator and
the ONNX network, the copies run as one batch.

At a fight decision where the network is unsure (its best option below CONF), its K best options (above 1%) are each
tried on N copies of the fight whose hidden information is drawn again (spire_sim._core.determinize: every random stream
reseeded, the draw pile shuffled). Each copy is played to the end of the fight by the network, greedily, and scored
by the network's value of the state right after it (score "value", the default), or by the ledger of that state:
HP fraction, potions held, gold and max HP at the act's prices (score "ledger"). A lost fight scores below every fight
that was not lost; a won game (the Heart) scores 1 with "value" (as in development), HEART_WIN with "ledger". All options are tried on
the same N copies, so they are compared under the same luck; the real draw order and random streams are never seen.
The option with the best average is played.

The same rule as the development project's fair search (only in elite and boss fights, k4 n16 conf0.9). How much it
adds in the real game has not been measured yet. "value" needs a model file with the value output; the ledger works with logits only. A model exported with
a variable batch size runs the copies together (one with batch 1 runs them one by one: the same result, slower).
"""
from __future__ import annotations

import time
from collections import Counter

import numpy as np

from .policy import BAGS, FIXED

ROOMS = {"ELITE", "BOSS"}  # elite includes Act 4's Shield and Spear
PICKS_FROM_DRAW_PILE = {"SECRET_TECHNIQUE", "SECRET_WEAPON", "SEEK"}  # options are draw pile positions
# act: (a full bar of HP, each potion, each 100 gold, each point of max HP), in whole-run return units
# (measured for the model C i2000, 2026-11-16)
LEDGER_PRICES = {
    1: (0.20, 0.024, 0.050, 0.005),
    2: (0.25, 0.029, 0.065, 0.007),
    3: (0.33, 0.037, 0.095, 0.011),
    4: (1.8, 0.11, 0.0, 0.018),
}
HEART_WIN = 5.0


def ledger_score(state: dict) -> float:
    """The ledger value of a state (Env.state()): HP fraction, potions held, gold and max HP at the act's prices."""
    hp_p, pot_p, gold_p, mhp_p = LEDGER_PRICES[min(4, max(1, int(state["act"])))]
    pots = sum(1 for p in state.get("potions", []) if "EMPTY" not in str(p[1]))
    return (hp_p * state["hp"] / max(1, state["max_hp"]) + pot_p * pots + gold_p * state["gold"] / 100
            + mhp_p * state["max_hp"])


def _pad(rows, dtype, tail=()):
    n = max(1, max(len(r) for r in rows))
    out = np.zeros((len(rows), n, *tail), dtype)
    mask = np.zeros((len(rows), n), bool)
    for i, r in enumerate(rows):
        r = np.asarray(r)
        if len(r):
            out[i, : len(r)] = r
            mask[i, : len(r)] = True
    return out, mask


def batch_many(fs: list) -> dict:
    """The model's inputs for several features dicts at once, each field padded to the longest (policy.batch_of for
    one is the same with a batch of one)."""
    b = {}
    for k in FIXED:
        v = np.stack([np.asarray(f[k]) for f in fs])
        b[k] = v.astype(np.float32) if v.dtype.kind == "f" else v.astype(np.int64)
    for k in BAGS:
        b[k], b[k + "_mask"] = _pad([f[k] for f in fs], np.int64)
    hn = [np.asarray(f["hand_num"], np.float32) for f in fs]
    b["hand_num"], _ = _pad(hn, np.float32, next((r.shape[1:] for r in hn if r.ndim > 1), ()))
    b["opt_kind"], b["opt_mask"] = _pad([f["opt_kind"] for f in fs], np.int64)
    b["opt_entity"], _ = _pad([f["opt_entity"] for f in fs], np.int64)
    on = [np.asarray(f["opt_num"], np.float32) for f in fs]
    b["opt_num"], _ = _pad(on, np.float32, next((r.shape[1:] for r in on if r.ndim > 1), ()))
    b["opt_entity2"], _ = _pad([f["opt_entity2"] if "opt_entity2" in f else np.zeros(len(f["opt_kind"]), np.int64)
                                for f in fs], np.int64)
    return b


class FairSearch:
    def __init__(self, sess, inputs: list, batched: bool, k: int = 4, n: int = 16, conf: float = 0.9,
                 max_steps: int = 400, seed: int = 0, determinize=None, score: str = "value"):
        """sess: an onnxruntime session of the model; batched: its batch size is variable (else one at a time).
        determinize: spire_sim's (the default), or another simulator build's for a comparison."""
        self.sess, self.inputs, self.batched = sess, inputs, batched
        self.k, self.n, self.conf, self.max_steps = k, n, conf, max_steps
        self.rng = np.random.default_rng(seed)
        self.stats = Counter()
        self.last = None  # the last search: options, scores, seconds (for the log)
        self.determinize = determinize
        if score not in ("value", "ledger"):
            raise ValueError(f"search score {score!r}: value or ledger")
        self.score = score

    def logits(self, fs: list) -> np.ndarray:
        """logits [len(fs), n_opt] (padding -inf)"""
        return self._run(fs, "logits")

    def values(self, fs: list) -> np.ndarray:
        """the network's value of each state [len(fs)]"""
        return self._run(fs, "value")

    def _run(self, fs: list, out_name: str) -> np.ndarray:
        if self.batched:
            b = batch_many(fs)
            return self.sess.run([out_name], {k: b[k] for k in self.inputs})[0]
        rows = [self.sess.run([out_name], {k: v for k, v in batch_many([f]).items() if k in self.inputs})[0][0]
                for f in fs]
        if out_name == "value":
            return np.asarray(rows, np.float32)
        out = np.full((len(rows), max(len(r) for r in rows)), -np.inf, np.float32)
        for i, r in enumerate(rows):
            out[i, : len(r)] = r
        return out

    def wants(self, env) -> bool:
        """a fight in an elite or boss room"""
        return str(env.space).endswith("BATTLE") and env.state().get("room") in ROOMS

    def choose(self, env, f: dict, logits: np.ndarray, best: int) -> int:
        """The option to play: best (the network's) unless the search finds a better one."""
        determinize = self.determinize
        if determinize is None:
            from spire_sim._core import determinize
        self.last = None
        x = np.exp((logits - logits.max()).astype(np.float32))
        p = x / x.sum()  # (float32, as FairSearch's torch.softmax)
        self.stats["battle_decisions"] += 1
        if (p > 0).sum() == 1 or p[best] >= self.conf:
            return best
        if env.state()["battle"].get("card_select_task") in PICKS_FROM_DRAW_PILE:
            return best
        order = np.argsort(-p)  # (as FairSearch: numpy's default sort)
        cand = [int(i) for i in order[: self.k] if p[i] > 0.01]
        if len(cand) < 2:
            return best
        t0 = time.perf_counter()
        self.stats["searched"] += 1
        envs = []
        for s in self.rng.integers(0, 2**63 - 1, size=self.n):
            base = determinize(env, int(s))
            for c in cand:
                e = base.clone()
                e.step(int(f["actions"][c]))
                envs.append(e)
        scores = self._rollout(envs).reshape(self.n, len(cand)).mean(0)
        pick = cand[int(scores.argmax())]
        if pick != best:
            self.stats["changed"] += 1
        self.last = {"score": self.score, "options": cand, "scores": [round(float(v), 4) for v in scores], "pick": pick, "network": best,
                     "seconds": round(time.perf_counter() - t0, 3), "copies": len(envs)}
        return pick

    def _rollout(self, envs) -> np.ndarray:
        scores = np.zeros(len(envs))
        lost = np.zeros(len(envs), dtype=bool)
        to_value, active = [], list(range(len(envs)))
        steps = 0
        while active:
            still = []
            for i in active:
                e = envs[i]
                if e.done:
                    scores[i] = (HEART_WIN if self.score == "ledger" else 1.0) if e.won else 0.0
                    lost[i] = not e.won
                elif not str(e.space).endswith("BATTLE") or steps >= self.max_steps:
                    to_value.append(i)
                else:
                    still.append(i)
            active = still
            if not active:
                break
            feats = [envs[i].features() for i in active]
            lg = self.logits(feats)
            for i, f, c in zip(active, feats, lg.argmax(-1).tolist()):
                envs[i].step(int(f["actions"][c]))
            steps += 1
        if to_value and self.score == "value":
            scores[to_value] = self.values([envs[i].features() for i in to_value])
        else:
            for i in to_value:
                scores[i] = ledger_score(envs[i].state())
        if lost.any() and not lost.all():
            low = scores[~lost].min()
            if low < 0:
                scores[lost] = low - 1.0
        self.stats["rollouts"] += len(envs)
        self.stats["rollout_steps"] += steps
        return scores
