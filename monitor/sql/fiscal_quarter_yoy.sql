-- Mean national index per Recruit fiscal quarter, and its year-over-year change (the earnings panel).
-- Recruit's fiscal year starts April 1: FY2025 = Apr 2025 – Mar 2026, Q1 = Apr–Jun.
-- SQLite date functions; in Snowflake / Trino swap strftime for YEAR() / MONTH().
WITH daily AS (
    SELECT date,
           index_sa AS idx,
           CAST(strftime('%Y', date) AS INTEGER)
             - (CAST(strftime('%m', date) AS INTEGER) < 4)            AS fy,
           ((CAST(strftime('%m', date) AS INTEGER) + 8) % 12) / 3 + 1  AS fq
    FROM postings_index
    WHERE jobcountry = 'US' AND variable = 'total postings'
), quarters AS (
    SELECT fy, fq, AVG(idx) AS mean_idx, COUNT(*) AS days
    FROM daily
    GROUP BY fy, fq
)
SELECT cur.fy,
       cur.fq,
       cur.days,
       cur.mean_idx,
       prev.mean_idx                  AS prev_year_mean_idx,
       cur.mean_idx / prev.mean_idx - 1 AS yoy
FROM quarters AS cur
JOIN quarters AS prev ON prev.fy = cur.fy - 1 AND prev.fq = cur.fq
ORDER BY cur.fy, cur.fq;
