"""Glossary, method appendix and references: one source for the app's About tab and the static build.

Every link here was checked to resolve (Sep 2026). data.indeed.com blocks scripted requests but is
the portal Hiring Lab's own README links to.
"""
from __future__ import annotations

import html as _html

# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
GLOSSARY = [
    ("Index", "Job postings on Indeed relative to Feb 1, 2020 = 100. A reading of 103.5 means postings are "
              "3.5% above that pre-pandemic level. Each sector is indexed to its own Feb 2020 level."),
    ("SA (seasonally adjusted)", "The predictable yearly rhythm (January hiring, holiday lulls, the school "
              "calendar) is removed, so any two dates compare fairly. Hiring Lab adjusts each series "
              "separately, using a Bundesbank method for daily data (adopted Nov 2024)."),
    ("7-day trailing average", "Each day's value is the average of the last 7 days, which smooths "
              "day-of-week noise."),
    ("Total vs new postings", "Total = every posting live on Indeed. New = postings live 7 days or fewer. New "
              "postings react to demand first."),
    ("YoY / change vs N weeks", "Index today ÷ index at the earlier date − 1. The Feb 2020 base cancels, so this "
              "is the exact % change in postings."),
    ("Breadth", "How many of the 47 sectors are up over the chosen period. Growth that's broad and growth that's "
              "concentrated in a few sectors tell different stories."),
    ("Normal variation (±3σ)", "σ = how much a series' readings wobble around their own 29-day trend over the "
              "last 3 years. A change counts as beyond normal variation only if it exceeds 3·√2·σ. A "
              "screening rule, not a significance test."),
    ("Control chart", "A time series with ±3σ limits (statistical process control). Points outside the limits "
              "are special causes: real news or a bad data load."),
    ("Grain", "What one row represents. Here: one row per date × sector × series. Violating it double-counts."),
    ("US ARPJ", "Average Revenue Per Job posting = Indeed's US HR Tech revenue ÷ total US job postings on Indeed "
              "(paid and free). Recruit discloses its year-over-year growth rate."),
    ("Sponsored Jobs pricing", "Employers pay per interaction, not per posting: per click to view, or per started "
              "application, depending on account and market. Prices come from Indeed's recommendation system "
              "(“market factors”). Free posts: up to 3 per month, live up to 30 days. Premium adds matching and "
              "time-to-hire features. (Indeed pricing page, Aug 2026)"),
    ("Recruit fiscal year", "Recruit Holdings (Indeed's parent) starts its fiscal year on April 1: "
              "FY2025 = Apr 2025 – Mar 2026. Q1 = Apr–Jun."),
]

EARNINGS_STEPS = [
    ("Recruit defines the KPI with this index",
     "“US ARPJ … is calculated by dividing HR Technology revenue in the US by the total number of US job "
     "postings on Indeed. … The denominator … is measured by the Indeed Hiring Lab US Job Postings Index.” "
     "(Recruit, Q1 FY2026 call)"),
    ("An index's growth rate is the count's growth rate",
     "I_t = 100·N_t / N_0, so I_t / I_s = N_t / N_s: the unpublished base N_0 cancels. The index gives postings "
     "growth exactly, but never the number of postings."),
    ("Revenue = postings × ARPJ is an identity",
     "(1 + g_revenue) = (1 + g_postings)·(1 + g_ARPJ). It's true by construction, not a correlation. Postings are "
     "the market; ARPJ is monetization. Q1 FY2026: 0.96 × 1.35 − 1 = +29.6%, vs +30.0% reported."),
    ("It checks out quarter by quarter",
     "The index YoY computed here lands within ~0.5 point of Recruit's reported postings change for FY2025 Q2 "
     "through FY2026 Q1 (see the earnings panel)."),
    ("Why Recruit discloses it",
     "CFO Junichi Arai: people view the business “purely through the lens of market conditions … unless we "
     "disclose something like US ARPJ, it becomes difficult to demonstrate … that even while the market is "
     "declining, revenue is still increasing.” (Q4 FY2025 call)"),
    ("Caveat: ARPJ is not price",
     "The denominator includes free and indexed postings, so ARPJ also moves with the mix of paid and free "
     "postings. Recruit: “not the average unit price per sponsored job ad.” (Q1 FY2026 call)"),
]

METHODS = [
    ("Data", "Hiring Lab US national and sector files, loaded into SQLite by build_hiringlab.py. Grain: date × "
             "sector × series. No counts are published."),
    ("Changes", "Point-to-point ratio of two published readings. The drill-through shows both rows and the "
                "arithmetic."),
    ("Normal variation", "σ = std of (index ÷ centered 29-day mean − 1) over the last 3 years; limit = 3·√2·σ "
                         "for a change between two readings. Colors appear only beyond the limit."),
    ("Control check", "Latest day-over-day change vs ±3σ of the series' own day-over-day changes over 3 years "
                      "(individuals / moving-range style). Day-over-day avoids the lag a trend line has at the "
                      "end of a series."),
    ("Earnings panel", "Index YoY per Recruit fiscal quarter = mean of daily index ÷ mean of the same quarter a year "
                       "earlier − 1. Recruit's figures are as stated on each call (mostly “approximately”)."),
    ("Data quality", "Pydantic row contracts plus dataset-level checks. Each check has a DAMA-DMBOK quality "
                     "dimension and a severity (block / warn). See the Data quality tab."),
]

LIMITATIONS = [
    "The index is the VOLUME side only: postings growth, i.e. ARPJ's denominator. It is not a revenue proxy. What "
    "employers pay (per click or per started application, set by Indeed's pricing system, plus Premium and other "
    "products) sits in ARPJ, which the index can't see. Revenue also depends on seeker engagement, which the index "
    "doesn't measure. Postings fell while revenue rose, which is exactly why Recruit reports the two separately.",
    "Postings are demand signals, not hires, employment or revenue. Openings can fall because positions were "
    "filled.",
    "Postings on Indeed, not the whole labor market. Platform changes can move the index independently of hiring "
    "demand.",
    "Index, not counts: rankings show growth, not where most jobs are.",
    "Seasonal adjustment is an estimate. Recent months can be revised, and history was restated in Nov 2024.",
    "Recruit's figures are rounded as stated on calls. Its exact quarterly aggregation isn't disclosed.",
]

REFERENCES = {
    "Data": [
        ("Indeed Hiring Lab — Job Postings Tracker (data + methodology)", "https://github.com/hiring-lab/job_postings_tracker"),
        ("Indeed Hiring Lab — research and analysis", "https://www.hiringlab.org/"),
        ("Indeed Hiring Lab — data FAQ", "https://www.hiringlab.org/indeed-data-faq/"),
        ("Indeed Data Portal (Hiring Lab)", "https://data.indeed.com/"),
        ("Deutsche Bundesbank — seasonal adjustment of daily time series (method used by Hiring Lab)",
         "https://www.bundesbank.de/resource/blob/763892/f5cd282cc57e55aca1eb0d521d3aa0da/mL/2018-10-17-dkp-41-data.pdf"),
        ("License: Creative Commons Attribution 4.0 (CC BY 4.0)", "https://creativecommons.org/licenses/by/4.0/"),
    ],
    "Earnings & monetization (Recruit Holdings, Indeed's parent)": [
        ("Indeed — How pricing works on Indeed: Sponsored Jobs, pay per click or per started application (updated Aug 2026)",
         "https://www.indeed.com/hire/resources/howtohub/how-pricing-works-on-indeed"),
        ("Investor Relations", "https://recruit-holdings.com/en/ir/"),
        ("Quarterly results & annual reports", "https://recruit-holdings.com/en/ir/financials/"),
        ("Q2 FY2025 earnings call transcript (Nov 2025): ARPJ introduced",
         "https://recruit-holdings.com/en/ir/library/upload/recruit_202603Q2_call-transcript_en/"),
        ("Q3 FY2025 earnings call transcript (Feb 2026)",
         "https://file.recruit-holdings.com/files/en/Recruit_202603Q3_call-transcript_en.pdf"),
        ("Q4 FY2025 earnings call transcript (May 2026): CFO on why ARPJ is disclosed",
         "https://file.recruit-holdings.com/files/en/Recruit_202603Q4_call-transcript_en.pdf"),
        ("Q1 FY2026 earnings call transcript (Aug 2026): ARPJ definition, index as denominator",
         "https://file.recruit-holdings.com/files/en/Recruit_202703Q1_call-transcript_en.pdf"),
    ],
    "Metrics at Indeed (engineering blog)": [
        ("M. Chen — Normalized Entropy or Apply Rate? Evaluation metrics for online experiments (2025)",
         "https://engineering.indeedblog.com/blog/2025/11/normalized-entropy-or-apply-rate-evaluation-metrics-for-online-modeling-experiments/"),
        ("J. Humphrey — Metrics-Driven Process Improvement: A Case Study (2018)",
         "https://engineering.indeedblog.com/blog/2018/10/metrics-driven-process-improvement-case-study/"),
        ("J. Humphrey — Accelerating Delivery with Metrics-Driven Insights (talk, 2020)",
         "https://engineering.indeedblog.com/talks/accelerating-delivery-with-metrics-driven-insights/"),
    ],
    "Visualization & data quality": [
        ("S. Few — Common Pitfalls in Dashboard Design (PDF)",
         "https://www.perceptualedge.com/articles/Whitepapers/Common_Pitfalls.pdf"),
        ("E. Tufte — The Visual Display of Quantitative Information, 2nd ed. (Graphics Press, 2001)", None),
        ("C. N. Knaflic — Storytelling with Data (Wiley, 2015)", None),
        ("A. Cairo — The Truthful Art (New Riders, 2016)", None),
        ("L. Sebastian-Coleman — Measuring Data Quality for Ongoing Improvement (Morgan Kaufmann, 2013)", None),
        ("DAMA International — DAMA-DMBOK: Data Management Body of Knowledge, 2nd ed. (2017)", None),
    ],
    "Tools": [
        ("Plotly Dash", "https://dash.plotly.com/"),
        ("Plotly for Python", "https://plotly.com/python/"),
        ("Pydantic", "https://docs.pydantic.dev/latest/"),
        ("pandas", "https://pandas.pydata.org/docs/"),
    ],
}

DISCLAIMER = ("Not affiliated with or endorsed by Indeed or Recruit Holdings. Built from public data for "
              "illustration. Source: Indeed Hiring Lab, CC BY 4.0.")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
def _link(label: str, url: str | None) -> str:
    esc = _html.escape(label)
    return (f'<a href="{_html.escape(url)}" target="_blank" rel="noopener noreferrer">{esc}</a> ↗'
            if url else esc)


def references_html() -> str:
    """References only, as an HTML block (static build footer)."""
    groups = "".join(
        f"<h3>{_html.escape(g)}</h3><ul>" + "".join(f"<li>{_link(l, u)}</li>" for l, u in items) + "</ul>"
        for g, items in REFERENCES.items())
    return f'<div class="refs">{groups}<p class="t-note">{_html.escape(DISCLAIMER)}</p></div>'


def about_html() -> str:
    """The full About page as HTML (Dash renders it through dcc.Markdown)."""
    gl = "".join(f"<dt>{_html.escape(t)}</dt><dd>{_html.escape(d)}</dd>" for t, d in GLOSSARY)
    steps = "".join(f"<li><b>{_html.escape(t)}.</b> {_html.escape(d)}</li>" for t, d in EARNINGS_STEPS)
    meth = "".join(f"<dt>{_html.escape(t)}</dt><dd>{_html.escape(d)}</dd>" for t, d in METHODS)
    lim = "".join(f"<li>{_html.escape(x)}</li>" for x in LIMITATIONS)
    return f"""
<div class="about">
  <div class="card"><h2>How to read this dashboard</h2><dl class="gloss">{gl}</dl></div>
  <div class="card"><h2>Why this index connects to Indeed's earnings</h2>
    <p class="sub">The claim, step by step, with its evidence and its limits.</p><ol class="steps">{steps}</ol></div>
  <div class="grid about-grid">
    <div class="card"><h2>Methods</h2><dl class="gloss">{meth}</dl></div>
    <div class="card"><h2>Limitations</h2><ul>{lim}</ul></div>
  </div>
  <div class="card"><h2>References &amp; links</h2>{references_html()}</div>
</div>"""


CSS = """
.about dl.gloss{display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:6px 16px;margin:4px 0}
.about dt{font-weight:600;color:var(--ink)} .about dd{margin:0;color:var(--ink2)}
.about ol.steps{padding-left:20px;margin:6px 0} .about ol.steps li{margin:6px 0;color:var(--ink2)}
.about ol.steps b{color:var(--ink)}
.about-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)!important}
.refs h3{font-size:13px;margin:12px 0 4px;color:var(--ink)} .refs ul{margin:0;padding-left:18px}
.refs li{margin:3px 0;color:var(--ink2)} .refs a{color:var(--series-up);text-decoration:none}
.refs a:hover{text-decoration:underline}
@media (max-width:720px){.about dl.gloss{grid-template-columns:1fr}.about dd{margin-bottom:6px}
  .about-grid{grid-template-columns:minmax(0,1fr)!important}}
"""
