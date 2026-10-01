-- grain: one row per pickup_date within the partition month
-- upstream: clean_trips (a single month partition)
-- year/month are carried by the output directory, never stored in the file.
SELECT
    pickup_date,
    any_value(pickup_dayofweek)                     AS day_of_week,
    any_value(is_weekend)                           AS is_weekend,
    count(*)                                        AS trip_count,
    sum(fare_amount)                                AS total_fare,
    sum(total_amount)                               AS total_revenue,
    sum(tip_amount)                                 AS total_tips,
    sum(trip_distance)                              AS total_distance_miles,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(tip_amount)                                 AS avg_tip,
    avg(trip_distance)                              AS avg_distance_miles,
    avg(trip_duration_minutes)                      AS avg_duration_minutes,
    quantile_cont(fare_amount, 0.5)                 AS median_fare
FROM ${source}
GROUP BY pickup_date
