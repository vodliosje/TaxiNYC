-- grain: one row per (year, month, rejection_reason, is_primary_reason)
-- upstream: rejected_trips
--
-- Two populations in one mart:
--   is_primary_reason = false -> every reason a row broke   (sums >= rejected rows)
--   is_primary_reason = true  -> the contract-priority reason (sums = rejected rows)
-- ${raw_rows} is bound from the reconciliation ledger for this month, so
-- share_of_raw is measured against what actually arrived, not what survived.
WITH src AS (
    SELECT rejection_reasons, primary_rejection_reason
    FROM ${source}
),
totals AS (
    SELECT count(*) AS rejected_rows FROM src
),
all_reasons AS (
    SELECT reason AS rejection_reason, false AS is_primary_reason, count(*) AS rejected_count
    FROM src, unnest(rejection_reasons) AS t(reason)
    GROUP BY 1
),
primary_reasons AS (
    SELECT primary_rejection_reason AS rejection_reason, true AS is_primary_reason,
           count(*) AS rejected_count
    FROM src
    WHERE primary_rejection_reason IS NOT NULL
    GROUP BY 1
),
combined AS (
    SELECT * FROM all_reasons
    UNION ALL
    SELECT * FROM primary_reasons
)
SELECT
    c.rejection_reason,
    c.is_primary_reason,
    c.rejected_count,
    c.rejected_count / nullif(t.rejected_rows, 0)::DOUBLE AS share_of_rejected,
    c.rejected_count / nullif(${raw_rows}, 0)::DOUBLE     AS share_of_raw
FROM combined c CROSS JOIN totals t
