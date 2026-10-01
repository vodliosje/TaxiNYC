-- grain: one row per (year, month, PULocationID)
SELECT
    PULocationID,
    count(*)                                        AS trip_count,
    sum(total_amount)                               AS total_revenue,
    sum(fare_amount)                                AS total_fare,
    sum(tip_amount)                                 AS total_tips,
    sum(trip_distance)                              AS total_distance_miles,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(trip_distance)                              AS avg_distance_miles,
    avg(trip_duration_minutes)                      AS avg_duration_minutes,
    avg(tip_rate)                                   AS avg_tip_rate
FROM ${source}
WHERE PULocationID IS NOT NULL
GROUP BY PULocationID
