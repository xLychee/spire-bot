"""The network (an ONNX file) on one decision at a time, with the last-chance key rule.

Out of fights the key rule may leave only some of the options; the network is then run on those and its best is
taken. In fights the network's best option is taken as it is.
"""
from __future__ import annotations

import time

import numpy as np

from .keys import allowed, restrict

FIXED = ("state_num", "player_status", "ctx_ids", "mon_ids", "mon_move", "mon_num")
BAGS = ("deck", "relics", "hand", "draw", "discard", "exhaust")
KINDS = ["BATTLE_CARD", "BATTLE_POTION", "BATTLE_END_TURN", "BATTLE_SELECT", "BATTLE_MULTI_SELECT", "EVENT_OPTION",
         "REWARD_GOLD", "REWARD_CARD", "REWARD_RELIC", "REWARD_KEY", "REWARD_POTION", "REWARD_SKIP", "BOSS_RELIC",
         "BOSS_RELIC_SKIP", "CARD_SELECT", "MAP_NODE", "TREASURE_OPEN", "TREASURE_SKIP", "REST_OPTION", "SHOP_CARD",
         "SHOP_RELIC", "SHOP_POTION", "SHOP_REMOVE", "SHOP_LEAVE", "NEOW_OPTION", "OTHER"]
MODEL_FORMAT = "spire-bot-model-1"
# Options whose scores are within TIE of the best count as tied, and the first of them (lowest index) is taken.
# Equivalent options (two copies of a card, say) get scores that differ only in the last bits, and those bits depend
# on the platform and the library version; with this rule every platform takes the same option.
TIE = 1e-4


def best(logits: np.ndarray) -> int:
    """The index of the best option: the first one within TIE of the highest score."""
    return int(np.flatnonzero(logits >= logits.max() - TIE)[0])


def _row(a, dtype):
    """one sample's variable-length field as a batch of one; an empty field becomes one padding element"""
    a = np.asarray(a)
    n = max(1, len(a))
    out = np.zeros((1, n, *a.shape[1:]), dtype)
    out[0, : len(a)] = a
    mask = np.zeros((1, n), bool)
    mask[0, : len(a)] = True
    return out, mask


def batch_of(f: dict) -> dict:
    """The model's inputs for one features dict (the simulator's Env.features())."""
    b = {}
    for k in FIXED:
        v = np.asarray(f[k])[None]
        b[k] = v.astype(np.float32) if v.dtype.kind == "f" else v.astype(np.int64)
    for k in BAGS:
        b[k], b[k + "_mask"] = _row(f[k], np.int64)
    b["hand_num"], _ = _row(f["hand_num"], np.float32)
    b["opt_kind"], b["opt_mask"] = _row(f["opt_kind"], np.int64)
    b["opt_entity"], _ = _row(f["opt_entity"], np.int64)
    b["opt_num"], _ = _row(f["opt_num"], np.float32)
    e2 = f["opt_entity2"] if "opt_entity2" in f else np.zeros(len(f["opt_kind"]), np.int64)
    b["opt_entity2"], _ = _row(e2, np.int64)
    return b


class Policy:
    def __init__(self, model_path: str, ruby_hp: float = 0.6, search: bool = False, search_threads: int = 4,
                 search_asc: int = 20, search_score: str = "value"):
        import onnxruntime as ort
        from spire_sim import entity_names
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        meta = self.sess.get_modelmeta().custom_metadata_map
        if meta.get("format") != MODEL_FORMAT:
            raise ValueError(f"{model_path}: not a {MODEL_FORMAT} model file (format {meta.get('format')!r})")
        self.meta = meta
        self.inputs = [i.name for i in self.sess.get_inputs()]
        # a model exported with its value heads has two more outputs, "value" and "bvalue" (the battle value head):
        # value is the expected return (+5 for the Heart, so value / 5 is about the network's own estimate of a Heart
        # win) and is the only one the panel reads; nothing is decided from either. Older files have logits only
        self.outputs = [o.name for o in self.sess.get_outputs()]
        self.params = int(meta["params"]) if str(meta.get("params", "")).isdigit() else None
        self.last_viz = None
        self.ruby_hp = ruby_hp
        self.names = {int(n["id"]): n["name"] for n in entity_names()}
        # the fair search in elite and boss fights (search.py): its own session, several threads, the
        # copies as one batch when the file's batch size is variable
        self.search, self.search_seed, self.search_asc, self.search_score = None, None, search_asc, search_score
        if search:
            so2 = ort.SessionOptions()
            so2.intra_op_num_threads = max(1, int(search_threads))
            so2.inter_op_num_threads = 1
            self.search_sess = ort.InferenceSession(str(model_path), so2, providers=["CPUExecutionProvider"])
            self.search_batched = not isinstance(self.sess.get_inputs()[0].shape[0], int)
            if search_score == "value" and "value" not in self.outputs:
                raise ValueError(f"{model_path}: search_score value needs a model file with the value output")

    def logits(self, f: dict) -> np.ndarray:
        return self.run(f)[0]

    def run(self, f: dict):
        """(logits, value or None) for one features dict"""
        b = batch_of(f)
        out = self.sess.run(None, {k: b[k] for k in self.inputs})
        v = float(out[self.outputs.index("value")][0]) if "value" in self.outputs else None
        return out[0][0], v

    def decide(self, env, seed: int):
        """(chosen option index, its probability, label, how) for the env's current decision."""
        from spire_sim import Space
        st, f = env.state(), env.features()
        t0 = time.perf_counter()
        lg, value = self.run(f)
        c, how = best(lg), "network"
        used, index = lg, list(range(len(lg)))  # the scores the choice was made from, and their option indices
        if env.space != Space.BATTLE:
            keep = allowed(st, f, self.ruby_hp, rule="last")
            if keep is not None:
                used, index = self.logits(restrict(f, keep)), list(keep)
                o = keep[best(used)]
                if o != c:
                    c, how = o, "key rule"
        fs = self._search_for(seed)
        if fs is not None:
            fs.last = None  # (the bridge logs fs.last after each decision: only this decision's search)
        if fs is not None and env.space == Space.BATTLE and fs.wants(env):
            o = fs.choose(env, f, lg, c)
            if fs.last is not None:
                how = "search"
                c = o
        ms = 1000 * (time.perf_counter() - t0)
        p = np.exp(lg - lg.max())
        # for the panel: the three best of the options chosen from, the choice first, with their softmax shares
        q = np.exp(used - used.max())
        q = q / q.sum()
        order = sorted(range(len(q)), key=lambda j: (index[j] != c, -q[j], j))[:3]
        self.last_f, self.last_top, self.last_restricted = f, [(index[j], float(q[j])) for j in order], len(index) < len(lg)
        # for the panel's thinking view: the five largest shares of the scores the choice was made from (highest
        # first; on equal shares the lower option first, so the chosen one leads a tie), and the option chosen. The
        # chosen option is always among them (in the fifth place if the tie rule took one below five others)
        top = sorted(range(len(q)), key=lambda j: (-q[j], index[j] != c, j))[:5]
        if all(index[j] != c for j in top):
            top[-1] = index.index(c)
        self.last_viz = {"top": [(index[j], float(q[j])) for j in top], "chosen": c, "pmax": float(q.max()),
                         "entropy": float(-(q[q > 0] * np.log(q[q > 0])).sum()), "n": len(q), "ms": ms,
                         "value": value, "how": how}
        return c, float(p[c] / p.sum()), self.label(env, f, c), how

    def _search_for(self, seed: int):
        """the run's FairSearch (its copies' random stream follows the run's seed, as in fair_gate), or None"""
        if not hasattr(self, "search_sess"):
            return None
        if self.search is None or self.search_seed != seed:
            from .search import FairSearch
            self.search = FairSearch(self.search_sess, self.inputs, self.search_batched,
                                     seed=int(seed) * 100 + self.search_asc, score=self.search_score)
            self.search_seed = seed
        return self.search

    def label(self, env, f, i: int) -> str:
        from spire_sim import Space
        a = int(f["actions"][i])
        if env.space == Space.BATTLE:
            return env.describe(a).strip("{} ")
        kind = KINDS[int(f["opt_kind"][i])].lower().replace("_", " ")
        return f"{kind}: {self.names.get(int(f['opt_entity'][i]), '')}".rstrip(": ")
