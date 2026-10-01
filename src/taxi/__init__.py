"""NYC taxi analytical data + ML platform.

Layering (imports only ever point downward):

    cli
     -> ingestion / marts / benchmark / ml / acquisition
     -> metadata, validation, contract
     -> core (duck, hashing, months, logging), config

`core` and `config` import nothing from the rest of the package, which keeps the
pure-logic layer testable without DuckDB installed.
"""

__version__ = "0.1.0"
