-- Change in each sector's index between two dates (the "Which sectors are growing?" ranking).
-- Ratios of an index cancel its Feb 1, 2020 base, so this is the exact % change in postings.
-- Params (named): :variable ('total postings' | 'new postings'), :latest, :ref  (ISO dates)
WITH now AS (
    SELECT sector, idx
    FROM postings_by_sector
    WHERE jobcountry = 'US' AND variable = :variable AND date = :latest
), ref AS (
    SELECT sector, idx
    FROM postings_by_sector
    WHERE jobcountry = 'US' AND variable = :variable AND date = :ref
)
SELECT now.sector,
       now.idx                AS idx_now,
       ref.idx                AS idx_ref,
       now.idx / ref.idx - 1  AS pct_change
FROM now
JOIN ref USING (sector)
ORDER BY pct_change DESC;
