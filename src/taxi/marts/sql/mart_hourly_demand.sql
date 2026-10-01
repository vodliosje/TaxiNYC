-- grain: one row per (year, month, hour_of_day, is_weekend)
-- year/month come from the output directory.
SELECT
    pickup_hour                                     AS hour_of_day,
    is_weekend,
    count(*)                                        AS trip_count,
    sum(total_amount)                               AS total_revenue,
    sum(fare_amount)                                AS total_fare,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(trip_duration_minutes)                      AS avg_duration_minutes,
    avg(avg_speed_mph)                              AS avg_speed_mph
FROM ${source}
GROUP BY pickup_hour, is_weekend
