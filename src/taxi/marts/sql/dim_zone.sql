-- grain: one row per LocationID. Not partitioned; refreshed in full.
SELECT
    CAST(LocationID AS INTEGER) AS location_id,
    CAST(Borough    AS VARCHAR) AS borough,
    CAST(Zone       AS VARCHAR) AS zone,
    CAST(service_zone AS VARCHAR) AS service_zone
FROM ${zones}
WHERE LocationID IS NOT NULL
