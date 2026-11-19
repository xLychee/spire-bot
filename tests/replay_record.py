"""Replays a recorded real game (a bridge record folder: states.jsonl.gz, autosaves.jsonl.gz, events.jsonl) through
the lockstep offline and returns what happened: the events the lockstep logged and whether it was in step at the end.

  python tests/replay_record.py RECORD_DIR SIM_SITE [MAX_STATES]

The record's own settings are used: the locked relics it logged, the bot's pace, resync on, the mouse's facing.
Developer tool; not part of the player's package.
"""
import gzip
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def open_record(record: Path, sim_site: str):
    """(bridge module, a new Lockstep, the record's in-game messages in order as (lockstep-ready) dicts). Feeding a
    message: call feed(msg), which hands the lockstep the autosaves the bridge had seen by then."""
    out = Path(tempfile.mkdtemp(prefix="replay_record_"))
    events = [json.loads(x) for x in open(record / "events.jsonl")]
    locked = next((e["locked"] for e in events if e["event"] == "locked_relics"), [])
    prefs = out / "preferences"
    prefs.mkdir()
    sys.path[:0] = [os.environ.get("REPLAY_BRIDGE") or str(ROOT / "bridge"), sim_site]  # (REPLAY_BRIDGE: another checkout's bridge folder)
    os.environ.update(CM_LOG_DIR=str(out / "logs"), CM_ADVICE=str(out / "bot.log"), SPIRE_GAME_DIR=str(out),
                      CM_PROFILE=str(prefs / "STSUnlocks"), CM_SAVES_DIR=str(out / "saves"), CM_PACE="bot",
                      CM_RESYNC="all", CM_PANEL="0", CM_MODEL="none")
    for name in [m for m in sys.modules if m == "spire_bot" or m.startswith("spire_bot.")]:
        del sys.modules[name]  # the bridge reads its settings when it is imported: import it afresh with these
    from spire_bot import bridge as B
    # a profile with exactly the record's relics locked (unlock value 2 = unlocked)
    (prefs / "STSUnlocks").write_text(json.dumps({r: "0" if r in locked else "2" for r in B.UNLOCKABLE_RELICS}))
    B.LOG_DIR.mkdir(parents=True, exist_ok=True)
    B.use_facing("mouse")
    lock = B.Lockstep(None)
    saves = []
    if (record / "autosaves.jsonl.gz").exists():
        saves = sorted(((s["t_state"], s["save"]) for s in map(json.loads, gzip.open(record / "autosaves.jsonl.gz", "rt"))),
                       key=lambda x: x[0])
    recs = [json.loads(line) for line in gzip.open(record / "states.jsonl.gz", "rt")]
    recs = [r for r in recs if r["msg"].get("in_game") and "game_state" in r["msg"]]

    # the bot's own decisions (auto.jsonl), so that the lockstep knows the action the bot sent, as the live bridge
    # does (Lockstep.prefer): before a state is fed, the newest decision made before it is looked up among the
    # simulator's legal actions by its description
    decisions = []
    if (record / "auto.jsonl").exists():
        for line in open(record / "auto.jsonl"):
            d = json.loads(line)
            if isinstance(d.get("info"), dict) and d.get("cmd"):
                decisions.append((d["t"], d["info"], d["cmd"]))
    used = [0]

    def set_prefer(t):
        info = None
        while used[0] < len(decisions) and decisions[used[0]][0] <= t:
            info, cmd = decisions[used[0]][1:3]
            used[0] += 1
            if info.get("swap_slot") is not None and cmd.startswith("potion discard ") and lock.env is not None:
                # a full-belt potion swap: the live bridge has every simulator branch discard the held potion when it
                # sends the game's discard (Auto, "potion swap: discard first"), so the game's next state fits.
                # Without it a replay logged mismatches the live game never had (5600448 floor 28: ElixirPotion in
                # the simulator only, twice)
                for cand in lock.cands:
                    cand.step(0xC0000000 | int(info["swap_slot"]))
                lock.env = lock.cands[0]
                info = None  # (the pick after the discard is the next decision)
        if info is None or lock.env is None or lock.env.done:
            return
        for a in lock.env.legal_actions():
            ai = json.loads(lock.env.action_info_json(a))
            if all(info.get(k) == v for k, v in ai.items()):
                lock.prefer = a
                return

    def feed(rec):
        while saves and saves[0][0] <= rec.get("t", 0):
            lock.latest_save = saves.pop(0)[1]
        set_prefer(rec.get("t", 0))
        lock.now = rec.get("t")  # the record's clock: how long a state has stood is read from it, not from this replay
        lock.on_state(rec["msg"])

    return B, lock, recs, feed


def replay(record: Path, sim_site: str, max_states: int | None = None) -> dict:
    B, lock, recs, feed = open_record(record, sim_site)
    n = 0
    for rec in recs:
        n += 1
        feed(rec)
        if max_states and n >= max_states:
            break
    ev = [json.loads(x) for x in open(B.LOG_DIR / "events.jsonl")] if (B.LOG_DIR / "events.jsonl").exists() else []
    mm = [json.loads(x) for x in open(B.LOG_DIR / "mismatch.jsonl")] if (B.LOG_DIR / "mismatch.jsonl").exists() else []
    return {"states": n, "synced": bool(lock.synced), "events": ev, "mismatches": mm, "prefer_set": lock.prefer is not None,
            "sim_in_battle": bool(lock.env is not None and (lock.env.state() or {}).get("battle")),
            "sim_floor": lock.env.floor if lock.env is not None else None}


if __name__ == "__main__":
    r = replay(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
    from collections import Counter
    print("states", r["states"], "in step at the end", r["synced"], "simulator in a fight", r["sim_in_battle"], "floor", r["sim_floor"])
    print("events", dict(Counter(e["event"] for e in r["events"])))
    print("escapes accepted on floors", [e.get("floor") for e in r["events"] if e["event"] == "smoke_bomb_escape_accepted"])
    print("mismatches", len(r["mismatches"]), [json.dumps(m["diff"])[:160] for m in r["mismatches"][-3:]])
