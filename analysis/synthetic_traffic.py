"""Drive synthetic visitors through a running deployment, end to end.

simulate.py validates the statistics in memory. This validates the *pipeline*:
real HTTP requests, real cookies, the server's own bucketing, exposure rows,
beacons, SQLite -- then report.py reads the result like any other experiment.
The true effect is set here, so the readout can be checked against it.

    python analysis/synthetic_traffic.py --url http://localhost:5000
    python analysis/synthetic_traffic.py --url https://ab.example.com --visitors 800 --lift 0.10

Point it ONLY at a deployment whose AB_EXPERIMENT is a demo name (the EC2 POC
defaults to exp001_demo). Synthetic rows in the pre-registered experiment
would void it: they are indistinguishable from real visitors once written.

Stdlib only, so it runs from any machine without the analysis dependencies.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar

# Deliberately not matching the app's bot filter, or nothing would be logged.
USER_AGENT = "ab-lab-synthetic/1.0 (demo traffic; see analysis/synthetic_traffic.py)"
TARGETS = ["total", "new", "yoy", "sectors", "trend"]
FIRST_BLOCK = re.compile(r'<div class="block (tiles|chart)"')


class Visitor:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        self.http.addheaders = [("User-Agent", USER_AGENT)]

    def visit(self) -> str:
        """Load the page; return the arm the SERVER chose, read off the layout."""
        with self.http.open(f"{self.base}/", timeout=20) as r:
            html = r.read().decode()
        m = FIRST_BLOCK.search(html)
        if not m:
            raise RuntimeError("couldn't tell the layout apart -- template changed?")
        return "A" if m.group(1) == "tiles" else "B"

    def send(self, event: str, target: str) -> None:
        req = urllib.request.Request(
            f"{self.base}/api/events", method="POST",
            data=json.dumps({"event": event, "target": target}).encode(),
            headers={"Content-Type": "application/json"})
        self.http.open(req, timeout=20).close()


def one_visitor(i: int, args, rates: dict[str, float]) -> tuple[str, bool]:
    rng = random.Random(args.seed * 100_003 + i)   # per-visitor, so threads stay reproducible
    v = Visitor(args.url)
    arm = v.visit()
    converted = rng.random() < rates[arm]
    if converted:
        v.send("detail_click", rng.choice(TARGETS))
        for _ in range(rng.choice([0, 0, 1, 2])):          # a few secondary clicks
            v.send("interaction", rng.choice(TARGETS))
    if rng.random() < args.return_rate:
        # A returning visitor must see the same layout. If not, stickiness is
        # broken and every number downstream is meaningless -- fail loudly.
        again = v.visit()
        if again != arm:
            raise RuntimeError(f"visitor {i} switched arms {arm} -> {again}")
    return arm, converted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--visitors", type=int, default=600)
    ap.add_argument("--baseline", type=float, default=0.30, help="true detail_ctr in A")
    ap.add_argument("--lift", type=float, default=0.12,
                    help="true absolute lift in B; 0 for an A/A test")
    ap.add_argument("--return-rate", type=float, default=0.2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20260921)
    args = ap.parse_args()

    rates = {"A": args.baseline, "B": args.baseline + args.lift}
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda i: one_visitor(i, args, rates), range(args.visitors)))

    print(f"Sent {len(results)} synthetic visitors to {args.url}")
    print(f"Ground truth: detail_ctr A = {rates['A']:.1%}, B = {rates['B']:.1%} "
          f"(lift {args.lift:+.1%})")
    for arm in "AB":
        conv = [c for a, c in results if a == arm]
        print(f"  {arm}: {len(conv):4d} visitors, {sum(conv):4d} converted "
              f"({sum(conv) / max(len(conv), 1):.1%} observed)")
    print("Returning visitors all kept their layout.")
    print("Now: deploy/down.sh (or copy the db), then "
          "python analysis/report.py --db <db> --experiment <AB_EXPERIMENT>")


if __name__ == "__main__":
    main()
