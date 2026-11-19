"""The bridge must end cleanly when a run is over while the game is still running.

main() returns at the run's end (bridge.py, RUN_OVER) while the thread that reads the game's lines (stdin_lines' pump)
still waits on stdin: the game keeps the pipe open. With the pump reading through sys.stdin.buffer, that thread holds
the BufferedReader's lock, and the interpreter's shutdown cannot close stdin: "Fatal Python error:
_enter_buffered_busy: could not acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter shutdown, possibly
due to daemon threads" (every game of a 20-run check, in communication_mod_errors.log). On the Mac that is an
abort; on Windows CPython 3.12's fatal_error then calls fatal_output_debug(NULL) and crashes in strlen (python312.dll
offset 0x272d6b, 0xc0000005, every game on a Windows PC), which Windows reports to the player.
"""
import os
import subprocess
import sys
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "bridge"

CHILD = r"""
import sys
sys.path.insert(0, %r)
from spire_bot.bridge import stdin_lines
q = stdin_lines()
print(q.get(timeout=10), end="", flush=True)
# the run is over: main() returns here while the game still holds the pipe open
"""


def run_child(tmp_path):
    env = dict(os.environ, CM_LOG_DIR=str(tmp_path / "logs"), CM_ADVICE=str(tmp_path / "bot.log"))
    p = subprocess.Popen([sys.executable, "-c", CHILD % str(BRIDGE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    p.stdin.write(b"{\"in_game\": false}\n")
    p.stdin.flush()
    try:
        code = p.wait(timeout=30)  # the pipe stays open, as the game's does
    finally:
        out, err = p.stdout.read(), p.stderr.read()
        p.stdin.close()
        if p.poll() is None:
            p.kill()
    return code, out.decode(), err.decode(errors="replace")


def test_bridge_ends_cleanly_while_the_game_still_writes(tmp_path):
    code, out, err = run_child(tmp_path)
    assert out == "{\"in_game\": false}\n"
    assert "Fatal Python error" not in err, err[-600:]
    assert code == 0, (code, err[-600:])


def test_lines_still_arrive_whole_and_in_order(tmp_path):
    """the reader splits the bytes into lines itself: a line written in pieces arrives whole, several in one write
    arrive one by one, the last one without a newline too, and None at the end"""
    code = r"""
import sys, os, time
sys.path.insert(0, %r)
from spire_bot.bridge import stdin_lines
q = stdin_lines()
got = []
while True:
    x = q.get(timeout=10)
    if x is None:
        break
    got.append(x)
print(repr(got))
""" % str(BRIDGE)
    env = dict(os.environ, CM_LOG_DIR=str(tmp_path / "logs"), CM_ADVICE=str(tmp_path / "bot.log"))
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env)
    for chunk in (b'{"a": 1', b'}\n{"b": 2}\n{"c": "\xe6\x89\x93', b'\xe5\x87\xbb"}\n', b"tail"):
        p.stdin.write(chunk)
        p.stdin.flush()
    p.stdin.close()
    p.wait(timeout=30)
    out, err = p.stdout.read(), p.stderr.read()
    assert p.returncode == 0, err.decode(errors="replace")[-600:]
    assert out.decode().strip() == repr(['{"a": 1}\n', '{"b": 2}\n', '{"c": "打击"}\n', "tail"])
