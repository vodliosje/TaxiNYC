"""Bucket edges are shared by SQL marts and Python segment analysis."""
from taxi.core.buckets import assign, case_sql, labels

EDGES = [0, 1, 2, 3, 5, 10, 20, 1000]


def test_final_bucket_is_open_ended():
    assert labels(EDGES, "mi")[-1] == "20+mi"


def test_sql_and_python_agree_on_every_label():
    sql = case_sql("trip_distance", EDGES, "mi")
    for label in labels(EDGES, "mi"):
        assert f"'{label}'" in sql


def test_assignment_uses_half_open_intervals():
    assert assign(1.0, EDGES, "mi") == "1-2mi"
    assert assign(0.999, EDGES, "mi") == "0-1mi"
    assert assign(None, EDGES, "mi") == "unknown"
