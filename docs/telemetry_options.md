# Telemetry: what's out there, and what this project uses

Written while deciding how to collect the events Experiment 001 needs.
Licenses are as understood at time of writing — verify before adopting.

## The landscape

### Session replay / heatmaps (the Hotjar shape)

| Tool | License | Notes |
|---|---|---|
| **rrweb** | MIT | The recorder itself — serializes DOM mutations + pointer movement and replays them like video. Most open-source replay tools are built on it. Use this if you want the capability without a platform. |
| **OpenReplay** | self-hostable, ELv2-family | Closest full Hotjar/FullStory equivalent you can run yourself. Replay, heatmaps, console/network capture, DOM masking built in. |
| **Highlight.io** | Apache-2.0 | Replay plus error monitoring and OpenTelemetry-native traces. |
| **Matomo** | GPL | GA replacement; heatmaps and recordings are a paid plugin, not core. |

### Product analytics + experimentation

| Tool | License | Notes |
|---|---|---|
| **PostHog** | MIT core (some dirs enterprise-licensed) | Autocapture, rrweb-based replay, feature flags, **and native A/B tests with a stats engine**. Self-hostable. Does this entire project out of the box. |
| **GrowthBook** | MIT | Purpose-built experimentation: feature flags, assignment, frequentist *and* Bayesian engines, SRM detection, CUPED variance reduction. Analyzes data in your own warehouse rather than ingesting it. |
| **Unleash / Flagsmith** | mixed OSS | Feature flags and targeting. Flags only — no stats engine, so analysis stays yours. |

### Privacy-first page analytics

| Tool | License | Notes |
|---|---|---|
| **Umami** | MIT | Cookieless, tiny, self-hosted, no PII by design. |
| **Plausible** | AGPL | Cookieless, GDPR-oriented. Originated the daily-rotating-salt visitor hash that this project borrows. |
| **Countly** | mixed OSS | Heavier; product + push + crash. |

### Standards

**OpenTelemetry** (Apache-2.0) is the vendor-neutral telemetry spec — traces,
metrics, logs, with a browser SDK. Worth knowing by name; overkill for counting
clicks in two arms.

## Decision for this project

**Build the collector; cite GrowthBook/PostHog as the production answer.**

Reasoning:

1. **A hand-rolled beacon is ~80 lines** — assign, stamp an exposure event, post
   clicks. The experiment needs four event types. Adopting a platform to get
   four event types means the interesting part is somebody else's code.
2. **Defensibility.** The project's claim is that I understand experimental
   design, not that I can install PostHog. Every field in the event schema has
   to be one I chose and can justify. That reverses if the goal is to
   demonstrate operating a platform at scale — which is the honest caveat to
   state out loud.
3. **Knowing the landscape is itself the answer to the interview question.**
   "I built the collector, and at real traffic I'd use GrowthBook so I'd get
   sequential testing, SRM alerting and CUPED instead of reimplementing them"
   is a better answer than either half alone.

## On mouse tracking and session replay

**Not in the primary design.** Two reasons, and the second one matters more.

**It isn't the decision data.** A heatmap is qualitative — good for generating
hypotheses, not for deciding between two arms. Experiment 001 is decided by one
pre-registered primary metric. Adding replay doesn't improve that decision.

**The multiple-comparisons trap.** Replay data hands you dozens of things to
compare between arms — hover time, rage clicks, scroll velocity, per-element
attention. Test enough of them at alpha = 0.05 and roughly one in twenty comes
back "significant" by chance alone. Mining a heatmap after a null primary
metric, and reporting whatever turned up, is textbook p-hacking. If replay is
added, it gets pre-registered as **secondary** and explicitly excluded from the
decision rule.

**And the privacy cost is real.** Session replay records what actual people do
on a screen. That is a categorically heavier obligation than counting clicks:

- Meaningful consent before recording, not a cookie banner afterward.
- Input masking is mandatory and fails open if misconfigured — rrweb's
  `maskAllInputs` plus explicit block classes on anything sensitive.
- Replays are personal data. They need retention limits, access control, and a
  deletion path.

For a personal site collecting a click-through rate, that trade is not worth
making.

## Privacy rules this project commits to

1. **One first-party cookie, and it is not optional.** A random UUID, no PII,
   no fingerprinting. Sticky assignment over a 28-day experiment *requires*
   persistence -- a rotating daily hash would let a visitor switch arms
   overnight, which is a confound, not a privacy win. So this project uses a
   cookie and says so plainly on the page rather than claiming a
   "cookieless" design it does not have.
2. **No PII.** Never collect name, email, or anything typed into an input.
3. **No raw IP at rest.** Either drop it, or store `hash(ip + user_agent +
   daily_salt)` with the salt rotating every 24h — the Plausible approach, which
   makes yesterday's visitors unlinkable to today's by construction.
4. **First-party only.** The beacon posts to this site's own domain. No
   third-party script, no data leaving the box.
5. **Data minimization.** The schema holds what the experiment needs and nothing
   speculative: visitor id, variant, event type, timestamp, clicked element,
   and a coarse device class (mobile / tablet / desktop -- never the user
   agent). If a field can't be tied to a metric in `experiment_design.md`, it
   isn't collected.
6. **Honor DNT and Global Privacy Control.** Signal present -> no assignment, no
   events, control layout served. Costs a little traffic; it's the right default
   and it's a one-line check.
7. **Published privacy note** on the site saying what's collected and why, in
   plain language.
8. **Retention limit.** Raw events expire after the experiment write-up is done.
   Keep the aggregates, drop the rows.

These aren't decoration — being able to explain why you collected *less* than
you could is the part of a data role that carries into production.
