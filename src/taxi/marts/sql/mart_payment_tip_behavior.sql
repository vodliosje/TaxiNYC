-- grain: one row per (year, month, payment_type, hour_of_day)
-- Caveat carried in the registry and on the dashboard: TLC records card tips only.
SELECT
    payment_type,
    pickup_hour                                     AS hour_of_day,
    count(*)                                        AS trip_count,
    count(*) FILTER (WHERE tip_amount > 0)          AS tipped_trip_count,
    sum(tip_amount)                                 AS total_tips,
    sum(fare_amount)                                AS total_fare,
    sum(total_amount)                               AS total_revenue,
    avg(tip_amount)                                 AS avg_tip,
    quantile_cont(tip_amount, 0.5)                  AS median_tip,
    avg(tip_rate)                                   AS avg_tip_rate,
    count(*) FILTER (WHERE tip_amount > 0) / nullif(count(*), 0)::DOUBLE
                                                    AS tip_incidence_rate,
    avg(total_amount)                               AS avg_total_amount
FROM ${source}
GROUP BY payment_type, pickup_hour
