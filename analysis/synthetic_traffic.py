"""Drive synthetic visitors through a running deployment, end to end.

simulate.py validates the statistics in memory. This validates the *pipeline*:
real HTTP requests, real cookies, the server's own bucketing, exposure rows,
the collector, SQLite -- then report.py reads the result like any other
experiment. The true effect is set here, so the readout can be checked against it.

Targets the Hiring Demand Monitor (monitor/): a visit is the page plus the
layout fetch that logs the exposure, and a conversion is a drill_through.

    python analysis/synthetic_traffic.py --url http://localhost:8050
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
TARGETS = ["rank", "trend", "mult", "sector-table", "earnings"]
# The first of these ids in the layout JSON says which arm the server picked.
FIRST_SECTION = re.compile(r'"id":\s*"(tiles|trend)"')


class Visitor:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()))
        self.http.addheaders = [("User-Agent", USER_AGENT)]

    def visit(self) -> str:
        """Load the page as a browser does (index, then the layout fetch that is the
        exposure); return the arm the SERVER chose, read off the section order."""
        self.http.open(f"{self.base}/", timeout=20).close()
        with self.http.open(f"{self.base}/_dash-layout", timeout=20) as r:
            layout = r.read().decode()
        m = FIRST_SECTION.search(layout)
        if not m:
            raise RuntimeError("couldn't tell the layouts apart -- did the section ids change?")
        return "A" if m.group(1) == "tiles" else "B"

    def send_time(self, rng: random.Random, engaged: bool) -> None:
        """Visible seconds for this page view, as assets/page_time.js would send it.
        The same rule in both arms (drillers stay longer), so any gap between arms
        comes only through the drill-through rate, plus noise. About 1 view in 10
        sends nothing, like a phone killing the tab."""
        if rng.random() < 0.1:
            return
        secs = rng.lognormvariate(4.0 if engaged else 3.2, 0.9)   # medians ~55 s / ~25 s
        self.send("page_time", str(min(round(secs), 3600)))

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
    v.send_time(rng, converted)
    if converted:
        v.send("drill_through", rng.choice(TARGETS))
        for _ in range(rng.choice([0, 0, 1, 2])):          # a few repeat drills
            v.send("drill_through", rng.choice(TARGETS))
    if rng.random() < args.return_rate:
        # A returning visitor must see the same layout. If not, stickiness is
        # broken and every number downstream is meaningless -- fail loudly.
        again = v.visit()
        if again != arm:
            raise RuntimeError(f"visitor {i} switched arms {arm} -> {again}")
        v.send_time(rng, False)
    return arm, converted


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--visitors", type=int, default=600)
    ap.add_argument("--baseline", type=float, default=0.30, help="true drill-through rate in A")
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
    print(f"Ground truth: drill-through rate A = {rates['A']:.1%}, B = {rates['B']:.1%} "
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
