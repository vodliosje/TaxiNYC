-- grain: one row per (year, month, DOLocationID)
SELECT
    DOLocationID,
    count(*)                                        AS trip_count,
    sum(total_amount)                               AS total_revenue,
    sum(fare_amount)                                AS total_fare,
    sum(trip_distance)                              AS total_distance_miles,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(trip_distance)                              AS avg_distance_miles,
    avg(trip_duration_minutes)                      AS avg_duration_minutes
FROM ${source}
WHERE DOLocationID IS NOT NULL
GROUP BY DOLocationID
