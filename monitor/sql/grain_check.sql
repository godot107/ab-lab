-- Grain check: the table must hold one row per date × sector × series.
-- Any row returned here double-counts every metric built on top of it.
SELECT date, sector, variable, COUNT(*) AS n_rows
FROM postings_by_sector
GROUP BY date, sector, variable
HAVING COUNT(*) > 1
ORDER BY n_rows DESC, date;
