-- grain: one row per (year, month, distance_bucket)
-- Bucket edges are rendered from configs/settings.yml (analysis.distance_buckets)
-- so this mart and the ML segment analysis always use identical bands.
SELECT
    ${distance_bucket}                              AS distance_bucket,
    count(*)                                        AS trip_count,
    sum(fare_amount)                                AS total_fare,
    sum(trip_distance)                              AS total_distance_miles,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    sum(fare_amount) / nullif(sum(trip_distance), 0) AS avg_fare_per_mile,
    avg(trip_duration_minutes)                      AS avg_duration_minutes,
    avg(avg_speed_mph)                              AS avg_speed_mph,
    quantile_cont(fare_amount, 0.5)                 AS median_fare,
    quantile_cont(fare_amount, 0.9)                 AS p90_fare
FROM ${source}
GROUP BY 1
