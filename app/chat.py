"""The "Ask about this data" box -- a single-turn Claude call grounded in the snapshot.

A proof of concept, built to be cheap before it is built to be clever:

- Single turn, no history. Every question costs the same small, fixed input
  (system prompt + snapshot, roughly 1.5K tokens) instead of growing per turn.
- Output capped by MAX_TOKENS and asked for in under ~120 words, at low effort.
- Quotas in SQLite, so both gunicorn workers share them: a per-visitor daily
  cap and a site-wide daily cap. The site-wide cap is the real cost ceiling.
- Off unless ANTHROPIC_API_KEY is set.

Nothing about chat is written to the events table. The chat box is identical
in both arms and is not an experiment metric; see docs/experiment_design.md.
Question text is sent to Anthropic to generate the answer and is not stored
here.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import anthropic

import store

MODEL = os.environ.get("CHAT_MODEL", "claude-opus-5")
MAX_TOKENS = int(os.environ.get("CHAT_MAX_TOKENS", "400"))
MAX_QUESTION_CHARS = 300
DAILY_LIMIT = int(os.environ.get("CHAT_DAILY_LIMIT", "100"))            # whole site
PER_VISITOR_LIMIT = int(os.environ.get("CHAT_PER_VISITOR_LIMIT", "5"))  # per visitor
# Server-side refusal fallback; only valid on models that support it.
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_quota (
    day   TEXT NOT NULL,     -- UTC date
    who   TEXT NOT NULL,     -- visitor id, 'anon' for no-cookie visits, '*' = site total
    n     INTEGER NOT NULL,
    PRIMARY KEY (day, who)
);
"""

_client: anthropic.Anthropic | None = None


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=30.0, max_retries=1)
    return _client


def init() -> None:
    with store.connect() as conn:
        conn.executescript(SCHEMA)


def take_quota(who: str) -> bool:
    """Atomically count one question against both caps. False if either is spent."""
    day = datetime.now(timezone.utc).date().isoformat()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")  # serialize across workers
        used = dict(conn.execute(
            "SELECT who, n FROM chat_quota WHERE day = ? AND who IN (?, '*')",
            (day, who)).fetchall())
        if used.get("*", 0) >= DAILY_LIMIT or used.get(who, 0) >= PER_VISITOR_LIMIT:
            return False
        conn.executemany(
            "INSERT INTO chat_quota (day, who, n) VALUES (?, ?, 1)"
            " ON CONFLICT (day, who) DO UPDATE SET n = n + 1",
            [(day, who), (day, "*")])
        return True


def system_prompt(snap: dict) -> str:
    rows = "\n".join(
        f"{s['name']}: index {s['index']:.1f}, {snap['recent_days']}-day change "
        f"{s['recent_change_pts']:+.1f} pts, vs. year ago {s['yoy_pct']:+.1f}%"
        for s in snap["sectors"])
    trend = ", ".join(f"{p['date']} {p['index']:.1f}" for p in snap["trend"])
    jolts = ", ".join(f"{p['date'][:7]} {p['index']:.1f} ({p['openings_k'] / 1000:.2f}M)"
                      for p in snap["jolts"])
    t, n = snap["total"], snap["new"]
    return f"""You answer questions about one dashboard: US job postings from the \
Indeed Hiring Lab Job Postings Index. The index is seasonally adjusted, a 7-day \
trailing average, with Feb 1 2020 = 100 (so 103 means postings are 3% above that \
pre-pandemic level). It measures posting volume, not hires or employment.

Answer only from the data below. If the question needs data that isn't here, say \
so in one sentence. Don't speculate about causes as fact; you may name plausible \
drivers if you label them as possibilities. Keep answers under 120 words, plain \
text, no headings. This is an independent portfolio project, not affiliated with \
Indeed.

Data as of {snap['as_of']}:
All postings: {t['index']:.1f} ({t['recent_change_pts']:+.1f} pts over \
{snap['recent_days']} days, {t['yoy_pct']:+.1f}% vs. a year ago)
New postings: {n['index']:.1f} ({n['recent_change_pts']:+.1f} pts over \
{snap['recent_days']} days, {n['yoy_pct']:+.1f}% vs. a year ago)
Sectors above Feb 2020 level: {snap['sectors_above_baseline']} of {len(snap['sectors'])}

By sector (all postings):
{rows}

All postings, weekly, last 12 months:
{trend}

Benchmark, BLS JOLTS job openings (total nonfarm, SA, monthly; re-based to Feb \
2020 = 100; counts openings at employers, not postings, and lags about two months):
{jolts}"""


def ask(question: str, snap: dict) -> str:
    """One question in, one short answer out. Raises anthropic.APIError on failure."""
    extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
        if MODEL in FALLBACK_MODELS else {}
    if not MODEL.startswith("claude-haiku"):  # Haiku 4.5 rejects `effort`
        extra["output_config"] = {"effort": "low"}
    response = client().beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt(snap),
        messages=[{"role": "user", "content": question}],
        **extra,
    )
    log.info("chat %s in=%d out=%d stop=%s", response._request_id,
             response.usage.input_tokens, response.usage.output_tokens,
             response.stop_reason)
    if response.stop_reason == "refusal":
        return "Sorry, I can't help with that one. Try a question about the postings data."
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += " …"
    return text or "No answer came back. Try rephrasing the question."
