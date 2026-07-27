CREATE OR REPLACE VIEW raw_yellow_trips AS
SELECT *
FROM read_parquet('data/raw/yellow/*.parquet');