"""Seed strings (what the game shows and `start ironclad 20 <SEED>` takes) <-> the 64-bit seed number (the
simulator's seed, and CommunicationMod's game_state["seed"] as a signed long).

A seed string is a number in base 35 written with the digits 0-9 and the letters A-Z without O (the game reads an
O as a zero).
Usage: python -m spire_bot.seed_string 12345 SPIRE2 ...   (numbers -> strings, strings -> numbers)"""
from __future__ import annotations

import sys

DIGITS = "0123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
MASK = 2**64 - 1


def to_number(s: str) -> int:
    """The seed number (unsigned 64-bit) of a seed string."""
    s = s.strip().upper().replace("O", "0")
    if not s:
        raise ValueError("empty seed string")
    n = 0
    for ch in s:
        k = DIGITS.find(ch)
        if k < 0:
            raise ValueError(f"seed string {s!r}: {ch!r} is not one of {DIGITS}")
        n = (n * 35 + k) & MASK  # the game's long arithmetic wraps the same way
    return n


def to_string(n: int) -> str:
    """The seed string of a seed number (a signed long from CommunicationMod is taken as unsigned)."""
    n &= MASK
    out = ""
    while True:
        n, r = divmod(n, 35)
        out = DIGITS[r] + out
        if n == 0:
            return out


def main(argv):
    for a in argv:
        if a.lstrip("-").isdigit():
            print(a, to_string(int(a)))
        else:
            print(a, to_number(a))


if __name__ == "__main__":
    main(sys.argv[1:])
