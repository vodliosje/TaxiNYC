# Data Quality & Cleaning Report: NYC Yellow Taxi Dataset

---

## 1. Dataset Overview

**Source Dataset:** NYC TLC Yellow Taxi Trip Records
**Analysis Period:** 2024-12-31 20:47:55 - 2025-02-01 00:00:44 (31 days 03:12:49)
**Raw Row Count:** 3475226 entries
**Clean Row Count:** 3330663
**Rejected Row Count:** 144563 (\_\_\_% of raw data)

---

## 2. Main Quality Issues Identified

1. **Unphysical Physics:** Negative or zero duration values ('tpep_dropoff_datetime' equal to or after 'tpep_pickup_datetime') and zero distances with possitive fare amounts.
2. **Meter Logging Errors:** Negative or zero fare amounts, negative tips, or impossbile calculated speeds (>80mph).
3. **Missing/Invalid Passenger Counts:** Records with '0' or 'NaN' passengers.

## 3. Cleaning Rules Applied

| `duration_min` | Must be > 0 minutes | Rejected if <= 0|
| `passenger_count` | Must be > 0 and not null | Rejected if 0 or null |
| `speed_mph` | Must between 0 and 80 mph | Rejected if > 80 or < 0 |

## 4. Rejected Record Breakdown

| Rejected Reason | Row Count | % of Raw Data | Business Justification |
| Negative Or 0 Duration | 2051 | | |
| Zero Distance trip | 90893 | | |
| No passenger | 24656 | | |
| Missing pickup dropoff location | 0 | | |

## 5. Anomaly (Retain Outliers)

1. **Extreme Distance (>= 50miles):** 490 trips. Kept because these represent legitimate long_distance out-of-city charter trips
2. **Extreme Fare (>=$300):** 2865 trips. Kept as valid high-value commercial runs

## 6. Dataset Limitations

1. Underreported Cash Tips
2. Lack of Exact lat/long coordinates
3. GPS Drift in Tall Corridors
4. No Traffic/ Weather context
