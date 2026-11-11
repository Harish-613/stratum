"""Measure a full Stratum scan against an incremental single-file update.

Usage (from the repo root, with the engine's venv active):
    PYTHONPATH=engine python benchmarks/bench_incremental.py /path/to/repo [more repos...]

For each repo: one full scan, then N single-file edits (a trailing comment is
appended to a real source file, re-analyzed, then reverted). Reports median
times. Files are restored even if the run is interrupted.
"""

import random
import statistics
import sys
import time
from pathlib import Path

from stratum_engine.session import Session

COMMENT = {"python": "#", "go": "//", "java": "//", "javascript": "//", "typescript": "//", "tsx": "//"}


def bench(root: str, edits: int = 15, seed: int = 0) -> dict:
    s = Session(root)
    t0 = time.perf_counter()
    report = s.scan()
    full = time.perf_counter() - t0
    full_timings = dict(s.last_timings)

    rng = random.Random(seed)
    candidates = sorted(p for p, info in s.files.items() if info["loc"] >= 20)
    picks = rng.sample(candidates, min(edits, len(candidates)))
    times = []
    for rel in picks:
        path = Path(s.root) / rel
        original = path.read_bytes()
        try:
            marker = COMMENT[s.files[rel]["language"]]
            path.write_bytes(original + f"\n{marker} stratum benchmark edit\n".encode())
            t = time.perf_counter()
            s.update([rel])
            times.append(time.perf_counter() - t)
        finally:
            path.write_bytes(original)
            s.update([rel])  # back to the original state (not timed)

    return {
        "repo": Path(root).name,
        "files": report["stats"]["files"],
        "loc": report["stats"]["loc"],
        "languages": report["stats"]["languages"],
        "full_s": full,
        "full_breakdown": full_timings,
        "update_median_ms": statistics.median(times) * 1000,
        "update_max_ms": max(times) * 1000,
    }


def main():
    for root in sys.argv[1:]:
        r = bench(root)
        langs = ", ".join(f"{k} {v}" for k, v in r["languages"].items())
        b = r["full_breakdown"]
        print(f"{r['repo']}: {r['files']:,} files, {r['loc']:,} LOC ({langs})")
        print(f"  full scan:      {r['full_s']:.2f} s  (parse {b['parse_s']:.2f} s, resolve {b['resolve_s']:.2f} s, graph {b['graph_s']:.2f} s)")
        print(f"  1-file update:  median {r['update_median_ms']:.0f} ms, max {r['update_max_ms']:.0f} ms")
        print(f"  speedup:        {r['full_s'] * 1000 / r['update_median_ms']:.0f}x")


if __name__ == "__main__":
    main()
