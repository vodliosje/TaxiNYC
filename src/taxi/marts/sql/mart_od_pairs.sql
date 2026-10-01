-- grain: one row per (year, month, PULocationID, DOLocationID)
-- Bounded at 265 x 265 pairs per month, so month grain stays small.
SELECT
    PULocationID,
    DOLocationID,
    count(*)                                        AS trip_count,
    sum(total_amount)                               AS total_revenue,
    sum(fare_amount)                                AS total_fare,
    sum(trip_distance)                              AS total_distance_miles,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(trip_distance)                              AS avg_distance_miles,
    avg(trip_duration_minutes)                      AS avg_duration_minutes,
    avg(avg_speed_mph)                              AS avg_speed_mph
FROM ${source}
WHERE PULocationID IS NOT NULL AND DOLocationID IS NOT NULL
GROUP BY PULocationID, DOLocationID
