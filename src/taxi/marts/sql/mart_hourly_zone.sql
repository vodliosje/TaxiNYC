-- grain: one row per (pickup_date, hour_of_day, PULocationID)
-- The densest mart: bounded by days x 24 x 265 rows per month.
SELECT
    pickup_date,
    pickup_hour                                     AS hour_of_day,
    PULocationID,
    count(*)                                        AS trip_count,
    sum(total_amount)                               AS total_revenue,
    sum(fare_amount)                                AS total_fare,
    sum(trip_duration_minutes)                      AS total_duration_minutes,
    avg(fare_amount)                                AS avg_fare,
    avg(trip_duration_minutes)                      AS avg_duration_minutes
FROM ${source}
WHERE PULocationID IS NOT NULL
GROUP BY pickup_date, pickup_hour, PULocationID
