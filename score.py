"""Stand-in scorer (Kypson's own score.py was not in the files received).

Usage:  python score.py my_tests.jsonl

Scores parse_rfq() from parse.py field by field: exact match, numbers compared
as numbers, location case-insensitive. Prints every failing message.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse import parse_rfq  # noqa: E402

KEYS = ["category", "quantity", "unit", "gsm", "micron", "location"]


def same(key, got, want):
    if got is None or want is None:
        return got is None and want is None
    if key == "location":
        return str(got).strip().lower() == str(want).strip().lower()
    if key in ("quantity", "gsm", "micron"):
        try:
            return abs(float(got) - float(want)) < 1e-6
        except (TypeError, ValueError):
            return False
    return got == want


def main():
    if len(sys.argv) < 2:
        print("usage: python score.py my_tests.jsonl")
        sys.exit(1)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    with open(sys.argv[1], encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    start = time.time()
    field_ok = {k: 0 for k in KEYS}
    all_ok = 0
    for row in rows:
        got = parse_rfq(row["text"])
        if set(got) != set(KEYS):
            print(f"BAD KEYS {row['text']!r}: {sorted(got)}")
        bad = [k for k in KEYS if not same(k, got.get(k), row["expected"].get(k))]
        for k in KEYS:
            field_ok[k] += k not in bad
        all_ok += not bad
        if bad:
            print(f"FAIL {row['text']!r}")
            for k in bad:
                print(f"     {k}: got {got.get(k)!r}  expected {row['expected'].get(k)!r}")

    n = len(rows)
    print(f"\n{n} messages in {time.time() - start:.2f}s")
    for k in KEYS:
        print(f"  {k:9s} {field_ok[k]}/{n}")
    print(f"  all six fields correct: {all_ok}/{n}")
    print(f"  field accuracy: {sum(field_ok.values()) / (6 * n):.1%}")


if __name__ == "__main__":
    main()
