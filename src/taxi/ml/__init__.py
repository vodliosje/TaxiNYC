"""Pre-trip fare estimation.

The modelling question is fixed by ML-PROBLEM.md: estimate `fare_amount` at the
moment a rider requests a trip, using only information that exists at that
moment. Every module here is arranged so that constraint is structural rather
than remembered - the feature table carries eval-only columns under an
`eval_` prefix, and each model variant declares its inputs explicitly.
"""
from taxi.ml.split import TemporalSplit, load_split  # noqa: F401
