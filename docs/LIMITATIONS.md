# Limitations

Written to be read by someone deciding whether to trust a number from this
system, and to be defensible when questioned.

## Data limitations

**Cash tips are invisible.** TLC populates `tip_amount` for card payments only.
Every tipping figure in `mart_payment_tip_behavior` and on the Tip Analysis page
is card-tipping behaviour. It is not tipping behaviour, and the dashboard says
so on the page rather than in a footnote.

**Trips have no identifier.** TLC publishes no trip id, so duplicates can only
be defined as byte-identical repeats within one source file. Two genuinely
distinct trips agreeing on every recorded field are indistinguishable here and
one of them is rejected as a duplicate. The rate is small but non-zero and is
visible in `mart_anomalies`.

**Zones, not coordinates.** Location is a taxi zone (265 of them), not a
lat/long. Distance and route effects inside a zone are invisible. A pickup in
one corner of a large outer-borough zone and one in the opposite corner are the
same feature value.

**Yellow medallion taxis only.** No green taxi, no for-hire vehicle, no
ride-hail. Conclusions about "NYC taxi demand" are conclusions about one
regulated segment of it, and that segment's share has been shrinking.

**The meter is the source of truth.** `trip_distance` is what the taximeter
reported, which is not the same as the distance driven, and both differ from
route distance. Fares reflect the meter, including time spent stationary.

**Vendor-reported fields are self-reported.** `passenger_count` is entered by
the driver and frequently NULL. It is retained under an explicit assumption
rather than treated as reliable.

## Pipeline limitations

**Rejection thresholds are provisional.** The values in `contracts/trips.yml`
are reasoned but not yet confirmed against full-year percentiles.
Run `taxi profile --months 2025-01..2025-12` and reconcile the contract with
what it shows before quoting any rejection rate as characteristic of the data.

**Rejection is per-row and stateless.** A trip that is individually plausible
but collectively impossible — the same vehicle in two boroughs a minute apart —
is not detected. Cross-row consistency checking would need a vehicle
identifier, which the data does not carry.

**One machine, one process.** DuckDB with a 6 GB memory limit and 4 threads.
This is sufficient for 48M rows and is a deliberate choice, but the system has
no story for concurrent writers, multi-user isolation, or datasets that exceed
local disk.

**Determinism is content-level, not byte-level.** See `BACKFILL.md`. Byte
equality across DuckDB versions is neither claimed nor tested.

**The manifest trusts the local file.** An upstream file that is silently
re-published with the same bytes but different meaning would not be detected.
TLC does re-publish months; the hash catches content changes, not semantic ones.

## Modelling limitations

**The target is `fare_amount`, not what a rider pays.** Tolls, surcharges,
congestion fees and tips are excluded on availability grounds. A rider-facing
quote would need all of them, and each one is a separate estimation problem.

**Two product assumptions, two models.** `origin_only` assumes a street hail
with no stated destination; `destination_known` assumes an in-app request. They
are not comparable to each other as models — they answer different questions —
and the second is only meaningful if the product really does collect a
destination first.

**The distance estimate is historical, not routed.** `od_median_distance` is
the median distance of that origin-destination pair in the training months. A
real pre-trip quote would call a routing service, which knows about the actual
requested route and current conditions. The feature is a stand-in and is fit on
training rows only.

**Training is sampled.** `ml.train_sample_rows_per_month` bounds training rows
per month (default 400,000) so experiments are reproducible in reasonable time.
Reported metrics are for that sample size and are not a claim about what a
full-data fit would produce.

**No fairness or incidence analysis.** Error is sliced by zone, hour, month,
distance and fare band. It is not analysed by any protected characteristic, and
zone-level error differences should not be read as a claim about neighbourhoods
or the people in them.

**2025 only.** No conclusion here transfers across a fare-schedule change,
which happens by regulation and not on a schedule this system knows about.

## What is not yet measured in this repository

Every performance and model number lives in `docs/generated/EVIDENCE.md` and is
produced by a run. If a section there says NOT MEASURED, that run has not
happened, and no figure for it should be quoted anywhere — including a CV.
