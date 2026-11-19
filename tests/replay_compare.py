"""Replays records with two versions of the bridge and compares them game by game.

  python tests/replay_compare.py SIM_SITE OTHER_BRIDGE_DIR OUT.json RECORD_DIR [RECORD_DIR ...]
  python tests/replay_compare.py SIM_SITE site:OTHER_SIM_SITE OUT.json RECORD_DIR [RECORD_DIR ...]

For each record: this repository's bridge and the one in OTHER_BRIDGE_DIR (another checkout's bridge/ folder) replay
it in separate processes (second form: this repository's bridge on two builds of the simulator); compared are the number of states, whether the lockstep was in step at the end, the
simulator's floor, and the events and mismatches it logged (kind, floor and content, in order). Developer tool.
REPLAY_COMPARE_IGNORE=field,field: event fields left out of the comparison (a count that is meant to differ).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

ONE = r"""
import json, sys
sys.path.insert(0, %r)
from replay_record import replay
from pathlib import Path
import os
IGNORE = tuple(x for x in os.environ.get("REPLAY_COMPARE_IGNORE", "").split(",") if x)  # event fields left out
r = replay(Path(sys.argv[1]), sys.argv[2])
ev = [{k: v for k, v in e.items() if k not in ("t", "seconds") + IGNORE} for e in r["events"]]
mm = [{k: v for k, v in m.items() if k != "t"} for m in r["mismatches"]]
print(json.dumps({"states": r["states"], "synced": r["synced"], "floor": r["sim_floor"], "in_battle": r["sim_in_battle"],
                  "events": ev, "mismatches": mm}, sort_keys=True))
""" % str(HERE)


def run(record, site, bridge):
    env = dict(os.environ)
    env.pop("REPLAY_BRIDGE", None)
    if bridge:
        env["REPLAY_BRIDGE"] = bridge
    out = subprocess.run([sys.executable, "-c", ONE, record, site], env=env, capture_output=True, text=True)
    lines = out.stdout.strip().splitlines()
    return json.loads(lines[-1]) if lines else {"error": out.stderr[-300:]}


def main():
    site, other, out = sys.argv[1:4]
    rows = []
    for rec in sys.argv[4:]:
        a, b = run(rec, site, None), (run(rec, other[5:], None) if other.startswith("site:") else run(rec, site, other))
        same = a == b
        row = {"record": rec, "same": same, "states": a.get("states"), "synced": a.get("synced"), "floor": a.get("floor"),
               "events": len(a.get("events", [])), "mismatches": len(a.get("mismatches", [])), "error": a.get("error") or b.get("error")}
        if not same:
            row["this"] = {k: a.get(k) for k in ("states", "synced", "floor")}
            row["other"] = {k: b.get(k) for k in ("states", "synced", "floor")}
            row["events_this_only"] = [e for e in a.get("events", []) if e not in b.get("events", [])][:5]
            row["events_other_only"] = [e for e in b.get("events", []) if e not in a.get("events", [])][:5]
            # the mismatch lines one side has and the other has not, whole, in order
            row["mismatches_this_only"] = [m for m in a.get("mismatches", []) if m not in b.get("mismatches", [])]
            row["mismatches_other_only"] = [m for m in b.get("mismatches", []) if m not in a.get("mismatches", [])]
            row["mismatches_other"] = len(b.get("mismatches", []))
        rows.append(row)
        print(("same     " if same else "DIFFERENT") + f" {rec}  states {row['states']} in step {row['synced']} floor {row['floor']}", flush=True)
    json.dump(rows, open(out, "w"), indent=1)
    print(f"{sum(r['same'] for r in rows)} of {len(rows)} records replay the same; errors {sum(bool(r['error']) for r in rows)}")


if __name__ == "__main__":
    main()
