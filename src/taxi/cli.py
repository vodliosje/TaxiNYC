"""Command-line interface.

Every operation the platform supports is reachable here and nowhere else: the
Makefile, the tests and the docs all drive the same entry points, so there is
one implementation of "ingest a month" rather than one per surface.

    taxi init
    taxi acquire   --months 2025-01..2025-12 [--synthetic]
    taxi profile   --months 2025-01
    taxi ingest    --months 2025-01..2025-03 [--force] [--no-downstream]
    taxi backfill  --from 2025-03 --to 2025-05
    taxi marts build [--only mart_daily_revenue] [--months ...]
    taxi marts list | taxi graph
    taxi bench run --label baseline
    taxi ml features | ml train | ml evaluate
    taxi report | taxi status | taxi doctor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from taxi import __version__
from taxi.config import Settings, load_settings
from taxi.core.logging import configure, get_logger
from taxi.core.months import Month, parse_month, parse_month_spec

log = get_logger("taxi.cli")

CHANGED_BY_INGEST = ("clean_trips", "rejected_trips")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _settings(args) -> Settings:
    return load_settings(args.settings)


def _store(settings):
    from taxi.metadata.store import MetadataStore

    return MetadataStore(settings)


def _contract(settings):
    from taxi.contract import Contract

    return Contract.load(settings.contract_path)


def _months(args, settings, fallback_to_clean: bool = True) -> list[Month]:
    if getattr(args, "months", None):
        return parse_month_spec(args.months)
    if fallback_to_clean:
        from taxi.marts.builder import partitions_in

        found = partitions_in(settings.paths.clean)
        if found:
            return found
    raise SystemExit("no --months given and no ingested partitions found")


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))


def _rebuild_downstream(settings, store, months: Sequence[Month], run_id: str, trigger: str):
    """Rebuild exactly the assets downstream of the layers a month ingest touched."""
    from taxi.marts.builder import affected_assets, build_assets
    from taxi.marts.registry import MartRegistry
    from taxi.ml.features import build_features

    registry = MartRegistry.load(settings.mart_registry_path)
    affected = affected_assets(registry, CHANGED_BY_INGEST)
    log.info("affected downstream assets: %s", ", ".join(affected))

    report = build_assets(
        settings, months=list(months), changed_assets=CHANGED_BY_INGEST,
        registry=registry, store=store, run_id=run_id, trigger=trigger,
    )
    rebuilt = list(report.assets)

    if "ml_features" in affected:
        results = build_features(settings, list(months), store=store, run_id=run_id)
        if results:
            rebuilt.append("ml_features")
    return rebuilt


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_init(args) -> int:
    settings = _settings(args)
    settings.paths.ensure()
    settings.source_dir().mkdir(parents=True, exist_ok=True)
    print(f"initialised data layout under {settings.root}")
    for directory in settings.paths.all_dirs():
        print(f"  {directory.relative_to(settings.root)}")
    return 0


def cmd_acquire(args) -> int:
    from taxi.acquisition.manifest import record_acquisition
    from taxi.acquisition.sources import acquire_months, acquire_reference
    from taxi.metadata.runs import RunRecorder

    settings = _settings(args)
    settings.paths.ensure()
    months = parse_month_spec(args.months)
    store = _store(settings)
    contract = _contract(settings)

    with RunRecorder(
        store=store, run_type="acquire",
        parameters={"months": [str(m) for m in months], "synthetic": args.synthetic},
        contract_fingerprint=contract.fingerprint,
    ) as run:
        run.source_partitions = [str(m) for m in months]
        if not args.no_reference:
            for ref in acquire_reference(settings, synthetic=args.synthetic, force=args.force):
                print(f"reference {ref['name']}: {ref['path']}"
                      f"{' (acquired)' if ref['acquired'] else ' (present)'}")
        files = acquire_months(
            settings, months, synthetic=args.synthetic, rows=args.rows,
            seed=args.seed, force=args.force,
        )
        record_acquisition(store, settings, files, run.run_id, contract.fingerprint)
        for source in files:
            print(f"{source.month}  {'acquired' if source.acquired else 'present '}  "
                  f"{source.size_bytes / 1e6:8.1f} MB  {source.path.name}")
    return 0


def cmd_profile(args) -> int:
    from taxi.metadata.runs import RunRecorder
    from taxi.validation.profile import profile_months

    settings = _settings(args)
    store = _store(settings)
    contract = _contract(settings)
    months = parse_month_spec(args.months)

    with RunRecorder(store=store, run_type="profile",
                     parameters={"months": [str(m) for m in months]},
                     contract_fingerprint=contract.fingerprint) as run:
        rows = profile_months(settings, months, contract=contract, store=store, run_id=run.run_id)

    interesting = [
        r for r in rows
        if r["column"] in {"trip_distance", "fare_amount", "trip_duration_minutes",
                           "avg_speed_mph", "total_amount", "tip_amount"}
    ]
    print(f"{'column':24s} {'p50':>10s} {'p99':>10s} {'p99.9':>10s} {'p99.99':>10s} {'max':>12s}")
    for row in interesting:
        print(f"{row['column']:24s} {_num(row['p50']):>10} {_num(row['p99']):>10} "
              f"{_num(row['p999']):>10} {_num(row['p9999']):>10} {_num(row['max_value']):>12}")
    print(f"\n{len(rows)} profile rows written to data/metadata/column_profile.parquet")
    return 0


def _num(value) -> str:
    return "-" if value is None else f"{value:,.2f}"


def cmd_ingest(args) -> int:
    from taxi.ingestion.pipeline import ingest_months

    settings = _settings(args)
    settings.paths.ensure()
    months = parse_month_spec(args.months)
    store = _store(settings)
    contract = _contract(settings)

    results = ingest_months(settings, months, contract=contract, store=store, force=args.force)
    for result in results:
        print(result.line())

    ingested = [r.month for r in results if r.status == "INGESTED"]
    if ingested and not args.no_downstream:
        run_id = results[-1].run_id
        rebuilt = _rebuild_downstream(settings, store, ingested, run_id, "month_changed")
        print(f"rebuilt downstream: {', '.join(rebuilt) or 'nothing'}")

    failed = [r for r in results if r.status == "FAILED"]
    return 1 if failed else 0


def cmd_backfill(args) -> int:
    from taxi.core.months import month_range
    from taxi.ingestion.backfill import backfill

    settings = _settings(args)
    store = _store(settings)
    months = month_range(parse_month(getattr(args, "from")), parse_month(args.to))

    from taxi.metadata.store import new_run_id

    rebuild_run_id = new_run_id("marts")

    def rebuild(changed_months, trigger):
        return _rebuild_downstream(settings, store, changed_months, rebuild_run_id, trigger)

    report = backfill(
        settings, months, reason=args.reason, store=store,
        rebuild=None if args.no_downstream else rebuild,
    )
    for comparison in report.comparisons:
        print(comparison.line())
    print(report.summary())
    if report.rebuilt_assets:
        print(f"rebuilt downstream: {', '.join(report.rebuilt_assets)}")
    return 0 if not report.changed else 2


def cmd_marts_build(args) -> int:
    from taxi.marts.builder import build_assets
    from taxi.metadata.runs import RunRecorder

    settings = _settings(args)
    store = _store(settings)
    months = parse_month_spec(args.months) if args.months else None

    with RunRecorder(store=store, run_type="marts",
                     parameters={"only": args.only, "months": args.months}) as run:
        report = build_assets(
            settings, months=months, only=args.only, changed_assets=args.changed,
            store=store, run_id=run.run_id,
            trigger="forced" if args.only else "full_rebuild",
        )
        for asset in report.assets:
            run.add_asset(asset)
    print(report.summary())
    return 0


def cmd_marts_list(args) -> int:
    from taxi.marts.registry import MartRegistry

    settings = _settings(args)
    registry = MartRegistry.load(settings.mart_registry_path)
    for spec in registry.marts:
        print(f"\n{spec.name}")
        print(f"  purpose : {spec.purpose}")
        print(f"  question: {spec.question}")
        print(f"  grain   : {spec.grain}")
        print(f"  dims    : {', '.join(spec.dimensions)}")
        print(f"  additive: {', '.join(spec.additive_measures)}")
        print(f"  derived : {', '.join(spec.non_additive_measures)}")
        print(f"  upstream: {', '.join(spec.upstream)}  refresh: {spec.refresh}")
        print(f"  consumers: {', '.join(spec.consumers)}")
    return 0


def cmd_graph(args) -> int:
    from taxi.marts.registry import MartRegistry

    settings = _settings(args)
    graph = MartRegistry.load(settings.mart_registry_path).graph
    if args.mermaid:
        print(graph.to_mermaid())
        return 0
    width = max(len(row["asset"]) for row in graph.describe())
    print(f"{'asset'.ljust(width)}  kind        upstream -> downstream")
    for row in graph.describe():
        print(f"{row['asset'].ljust(width)}  {row['kind']:<10s}  "
              f"{row['upstream']} -> {row['downstream']}")
    if args.changed:
        affected = graph.downstream_of(list(args.changed))
        print(f"\nchanging {', '.join(args.changed)} requires rebuilding:")
        for name in affected:
            print(f"  {name}")
    return 0


def cmd_bench(args) -> int:
    from taxi.benchmark.suite import run_suite
    from taxi.metadata.runs import RunRecorder

    settings = _settings(args)
    store = _store(settings)
    with RunRecorder(store=store, run_type="benchmark",
                     parameters={"label": args.label, "only": args.only}) as run:
        results = run_suite(settings, label=args.label, only=args.only,
                            repeats=args.repeats, store=store, run_id=run.run_id)
    print()
    for result in results:
        print(result.line())
    mismatches = [r for r in results if r.comparison.startswith("MISMATCH")]
    if mismatches:
        print(f"\n{len(mismatches)} query variant(s) disagreed - see output above")
        return 2
    return 0


def cmd_ml_features(args) -> int:
    from taxi.metadata.runs import RunRecorder
    from taxi.ml.features import build_features

    settings = _settings(args)
    store = _store(settings)
    months = parse_month_spec(args.months) if args.months else None
    with RunRecorder(store=store, run_type="features",
                     parameters={"months": args.months}) as run:
        results = build_features(settings, months, store=store, run_id=run.run_id)
        run.rows_accepted = sum(r.rows for r in results)
    for result in results:
        print(result.line())
    return 0


def cmd_ml_train(args) -> int:
    from taxi.metadata.runs import RunRecorder
    from taxi.ml.train import train_and_evaluate

    settings = _settings(args)
    store = _store(settings)
    with RunRecorder(store=store, run_type="ml_train",
                     parameters={"variants": args.variants, "models": args.models}) as run:
        report = train_and_evaluate(
            settings, variants=args.variants, models=args.models,
            store=store, run_id=run.run_id,
        )
    print("\n" + report.table().to_string(index=False))
    print(f"\nselected: {report.selected}\nreason  : {report.selection_reason}")
    return 0


def cmd_ml_evaluate(args) -> int:
    settings = _settings(args)
    store = _store(settings)
    rows = store.query(
        """
        SELECT model, variant, split, n_eval, round(mae,4) AS mae, round(rmse,4) AS rmse,
               round(r2,4) AS r2
        FROM ml_evaluations
        WHERE evaluated_at = (SELECT max(evaluated_at) FROM ml_evaluations)
        ORDER BY variant, split, mae
        """
    )
    if not rows:
        print("no evaluations recorded - run `taxi ml train` first")
        return 1
    widths = {k: max(len(k), *(len(str(r[k])) for r in rows)) for k in rows[0]}
    print("  ".join(k.ljust(widths[k]) for k in rows[0]))
    for row in rows:
        print("  ".join(str(row[k]).ljust(widths[k]) for k in row))

    worst = store.query(
        """
        SELECT model, variant, segment_kind, segment_value, n, round(mae,3) AS mae,
               round(bias,3) AS bias
        FROM ml_segment_errors
        WHERE split = 'test'
          AND evaluated_at = (SELECT max(evaluated_at) FROM ml_segment_errors)
        ORDER BY mae DESC LIMIT 10
        """
    )
    print("\nworst test segments by MAE:")
    for row in worst:
        print(f"  {row['model']:<24s} {row['segment_kind']:<16s} "
              f"{row['segment_value']:<28s} n={row['n']:>8,}  MAE={row['mae']}  "
              f"bias={row['bias']}")
    return 0


def cmd_report(args) -> int:
    from taxi.report import EvidenceReport

    settings = _settings(args)
    target = EvidenceReport(settings).write()
    print(f"wrote {target}")
    return 0


def cmd_status(args) -> int:
    from taxi.acquisition.manifest import current_state
    from taxi.marts.builder import partitions_in
    from taxi.marts.registry import MartRegistry

    settings = _settings(args)
    store = _store(settings)
    paths = settings.paths

    print(f"nyc-taxi {__version__}   root={settings.root}")
    print(f"contract: {settings.contract_path.name} ({_contract(settings).fingerprint})")

    state = current_state(store)
    ingested = [k for k, v in state.items() if v.get("ingestion_status") == "INGESTED"]
    print(f"\nsources tracked : {len(state)}  ingested: {len(ingested)}")
    print(f"clean partitions: {len(partitions_in(paths.clean))}")
    print(f"rejected parts  : {len(partitions_in(paths.rejected))}")
    print(f"feature parts   : {len(partitions_in(paths.features))}")

    registry = MartRegistry.load(settings.mart_registry_path)
    built = [s.name for s in registry.buildable if (paths.mart_dir(s.name)).exists()]
    print(f"marts built     : {len(built)}/{len(registry.buildable)}"
          f"{'  (' + ', '.join(built) + ')' if built else ''}")

    totals = store.query(
        """
        SELECT sum(raw_rows) AS raw, sum(clean_rows) AS clean, sum(rejected_rows) AS rejected,
               bool_and(reconciliation_passed) AS ok
        FROM (SELECT * EXCLUDE (__rn) FROM (
                SELECT raw_rows, clean_rows, rejected_rows, reconciliation_passed,
                       row_number() OVER (PARTITION BY year, month
                         ORDER BY checked_at DESC) AS __rn
                FROM reconciliation) WHERE __rn = 1)
        """
    )
    if totals and totals[0]["raw"]:
        row = totals[0]
        print(f"\nrows raw={int(row['raw']):,}  clean={int(row['clean']):,}  "
              f"rejected={int(row['rejected']):,}  reconciled={row['ok']}")

    runs = store.query(
        "SELECT run_id, run_type, final_status, round(duration_seconds,2) AS seconds "
        "FROM pipeline_runs ORDER BY start_time DESC LIMIT 5"
    )
    if runs:
        print("\nrecent runs:")
        for row in runs:
            print(f"  {row['run_id']}  {row['run_type']:<10s} {row['final_status']:<8s} "
                  f"{row['seconds']}s")
    return 0


def cmd_doctor(args) -> int:
    settings = _settings(args)
    problems = 0

    print("environment")
    for module in ("duckdb", "pyarrow", "pandas", "numpy", "sklearn", "yaml"):
        try:
            imported = __import__(module)
            print(f"  ok      {module} {getattr(imported, '__version__', '?')}")
        except ImportError:
            print(f"  MISSING {module}")
            problems += 1
    for module, extra in (("streamlit", "dashboard"), ("pytest", "dev")):
        try:
            __import__(module)
            print(f"  ok      {module}")
        except ImportError:
            print(f"  absent  {module} (optional: pip install -e \".[{extra}]\")")

    print("\nconfiguration")
    for label, path in (("settings", settings.root / "configs/settings.yml"),
                        ("contract", settings.contract_path),
                        ("marts", settings.mart_registry_path),
                        ("benchmarks", settings.benchmark_path)):
        state = "ok     " if path.is_file() else "MISSING"
        problems += 0 if path.is_file() else 1
        print(f"  {state} {label}: {path}")

    print("\ncontract integrity")
    try:
        contract = _contract(settings)
        print(f"  ok      {len(contract.columns)} columns, {len(contract.derived)} derived, "
              f"{len(contract.rules)} rules")
        declared = {name for name, _ in contract.declared_ranges()}
        print(f"  ok      {len(declared)} columns declare a valid_range")
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR   {exc}")
        problems += 1

    print("\nregistry integrity")
    try:
        from taxi.marts.registry import MartRegistry

        registry = MartRegistry.load(settings.mart_registry_path)
        graph = registry.graph
        print(f"  ok      {len(registry.marts)} marts, {len(graph.assets)} graph nodes, acyclic")
        for spec in registry.buildable:
            spec.sql()
        print(f"  ok      {len(registry.buildable)} SQL files present")
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR   {exc}")
        problems += 1

    print(f"\n{problems} problem(s)")
    return 1 if problems else 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taxi", description=__doc__.split("\n")[0])
    parser.add_argument("--settings", help="path to settings.yml (default: configs/settings.yml)")
    parser.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")
    parser.add_argument("--version", action="version", version=f"nyc-taxi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the data directory layout").set_defaults(func=cmd_init)

    acquire = sub.add_parser("acquire", help="fetch or generate monthly source files")
    acquire.add_argument("--months", required=True, help="e.g. 2025-01 or 2025-01..2025-12")
    acquire.add_argument("--synthetic", action="store_true",
                         help="generate deterministic stand-in months instead of downloading")
    acquire.add_argument("--rows", type=int, default=250_000, help="rows per synthetic month")
    acquire.add_argument("--seed", type=int, default=42)
    acquire.add_argument("--force", action="store_true", help="re-acquire existing files")
    acquire.add_argument("--no-reference", action="store_true", help="skip the zone lookup")
    acquire.set_defaults(func=cmd_acquire)

    profile = sub.add_parser("profile", help="profile raw column distributions (threshold evidence)")
    profile.add_argument("--months", required=True)
    profile.set_defaults(func=cmd_profile)

    ingest = sub.add_parser("ingest", help="validate a month into clean + rejected")
    ingest.add_argument("--months", required=True)
    ingest.add_argument("--force", action="store_true", help="ingest even if nothing changed")
    ingest.add_argument("--no-downstream", action="store_true",
                        help="do not rebuild affected marts and features")
    ingest.set_defaults(func=cmd_ingest)

    backfill = sub.add_parser("backfill", help="recompute historical months deterministically")
    backfill.add_argument("--from", required=True, dest="from")
    backfill.add_argument("--to", required=True)
    backfill.add_argument("--reason", default="manual backfill")
    backfill.add_argument("--no-downstream", action="store_true")
    backfill.set_defaults(func=cmd_backfill)

    marts = sub.add_parser("marts", help="build or inspect analytical marts")
    marts_sub = marts.add_subparsers(dest="marts_command", required=True)
    build = marts_sub.add_parser("build")
    build.add_argument("--only", nargs="*", help="asset names to build")
    build.add_argument("--changed", nargs="*",
                       help="assets that changed; rebuilds their dependents only")
    build.add_argument("--months", help="restrict to these months")
    build.set_defaults(func=cmd_marts_build)
    marts_sub.add_parser("list").set_defaults(func=cmd_marts_list)

    graph = sub.add_parser("graph", help="show the asset dependency graph")
    graph.add_argument("--mermaid", action="store_true")
    graph.add_argument("--changed", nargs="*", help="show what a change to these assets rebuilds")
    graph.set_defaults(func=cmd_graph)

    bench = sub.add_parser("bench", help="run the benchmark workloads")
    bench_sub = bench.add_subparsers(dest="bench_command", required=True)
    bench_run = bench_sub.add_parser("run")
    bench_run.add_argument("--label", default="default")
    bench_run.add_argument("--only", nargs="*")
    bench_run.add_argument("--repeats", type=int, default=None)
    bench_run.set_defaults(func=cmd_bench)

    ml = sub.add_parser("ml", help="feature build, training and evaluation")
    ml_sub = ml.add_subparsers(dest="ml_command", required=True)
    features = ml_sub.add_parser("features")
    features.add_argument("--months")
    features.set_defaults(func=cmd_ml_features)
    train = ml_sub.add_parser("train")
    train.add_argument("--variants", nargs="*")
    train.add_argument("--models", nargs="*")
    train.set_defaults(func=cmd_ml_train)
    ml_sub.add_parser("evaluate").set_defaults(func=cmd_ml_evaluate)

    sub.add_parser("report", help="regenerate docs/generated/EVIDENCE.md").set_defaults(
        func=cmd_report
    )
    sub.add_parser("status", help="one-screen system state").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="check environment and configuration").set_defaults(
        func=cmd_doctor
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure(args.log_level)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130
    except BrokenPipeError:
        # `taxi marts list | head` closes the pipe; that is not an error.
        try:
            sys.stdout.close()
        finally:
            return 0
    except ModuleNotFoundError as exc:
        log.error("%s. Run `pip install -e .` then `taxi doctor`.", exc)
        return 1
    except (FileNotFoundError, RuntimeError, ValueError, KeyError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
