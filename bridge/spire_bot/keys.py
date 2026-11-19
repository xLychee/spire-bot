"""Rule-based key collection. Heart wins need all three keys. The rule narrows the options instead of
overriding the choice, so a policy trained with it in place sees exactly the options it chose from:

  - emerald: while missing, map nodes from which the burning elite is unreachable are dropped (if any node can
    reach it), and its key reward is the only option on that screen;
  - sapphire: while missing, the chest's key is the only option (the relic is given up);
  - ruby: while missing, RECALL is the only rest option when HP >= ruby_hp of max from Act 2 on, or from floor 49.

allowed(state, f) -> indices to keep, or None for no restriction; restrict(f, keep) -> features with only those.
The rule only uses what the player sees (the map, the keys held, HP).

rule="last": emerald and sapphire are forced only once the run is in Act 3 (in Acts 1-2 the rule treats them as
held, so it never narrows choices for them), ruby only from floor 49 (the rest site before the Act 3 boss), never by
HP. The default rule="first" is the rule above.
"""

from __future__ import annotations

REWARD_KEY, MAP_NODE, REST_OPTION = 9, 15, 18  # indices into the option-kind list
BURNING_COL = 8 + 6  # opt_num column on a map node: the burning elite is reachable from it
SAPPHIRE_COL = 6  # opt_num column on a key reward: it is the sapphire (costs the chest relic)
OPTION_KEYS = ("opt_kind", "opt_entity", "opt_num", "opt_entity2", "actions")
_RECALL = None


def _recall_id() -> int:
    global _RECALL
    if _RECALL is None:
        from spire_sim import entity_names
        _RECALL = next(n["id"] for n in entity_names() if n["category"] == 6 and n["enum"] == "RECALL")
    return _RECALL


RULES = ("first", "last")


def allowed(st: dict, f: dict, ruby_hp: float = 0.6, recall_id: int | None = None, rule: str = "first"):
    keys = st["keys"]
    if rule == "last":
        if st["act"] < 3:
            keys = dict(keys, emerald=True, sapphire=True)
        ruby_hp = float("inf")  # only the floor >= 49 condition can force the ruby
    elif rule != "first":
        raise ValueError(f"key rule {rule!r}, expected one of {RULES}")
    kinds = [int(k) for k in f["opt_kind"]]
    n = len(kinds)
    if n <= 1:
        return None
    for i, k in enumerate(kinds):
        if k == REWARD_KEY:
            sapphire = f["opt_num"][i][SAPPHIRE_COL] > 0.5
            if (sapphire and not keys["sapphire"]) or (not sapphire and not keys["emerald"]):
                return [i]
    if not keys["ruby"] and REST_OPTION in kinds and (
            (st["act"] >= 2 and st["hp"] >= ruby_hp * st["max_hp"]) or st["floor"] >= 49):
        rid = _recall_id() if recall_id is None else recall_id
        recall = [i for i, k in enumerate(kinds) if k == REST_OPTION and int(f["opt_entity"][i]) == rid]
        if recall:
            return recall
    if not keys["emerald"] and all(k == MAP_NODE for k in kinds):
        ok = [i for i in range(n) if f["opt_num"][i][BURNING_COL] > 0.5]
        if ok and len(ok) < n:
            return ok
    return None


def restrict(f: dict, keep: list[int]) -> dict:
    g = dict(f)
    for k in OPTION_KEYS:
        g[k] = f[k][keep]
    return g
