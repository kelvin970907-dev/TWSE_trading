from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from pathlib import Path


def load_runner_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_databricks_daily_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_databricks_daily_pipeline", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_write_and_append_text(tmp_path: Path) -> None:
    runner = load_runner_module()
    log_path = tmp_path / "logs" / "databricks.log"

    runner.safe_write_text(log_path, "first\n")
    runner.safe_append_text(log_path, "second\n")

    assert log_path.read_text(encoding="utf-8") == "first\nsecond\n"


def test_success_copies_db_to_scratch_syncs_back_and_copies_reports(tmp_path: Path) -> None:
    runner = load_runner_module()
    root = tmp_path / "persistent"
    scratch = tmp_path / "scratch"
    persistent_db = root / "data" / "taiwan_trading.duckdb"
    persistent_db.parent.mkdir(parents=True)
    persistent_db.write_text("old-db", encoding="utf-8")
    existing_report = root / "reports" / "live_signals" / "existing.csv"
    existing_report.parent.mkdir(parents=True)
    existing_report.write_text("existing-report", encoding="utf-8")

    def fake_pipeline(**kwargs):
        scratch_db = Path(kwargs["db_path"])
        scratch_output = Path(kwargs["output_dir"])
        assert scratch_db == scratch / "data" / "taiwan_trading.duckdb"
        assert scratch_db.read_text(encoding="utf-8") == "old-db"
        assert (scratch_output / "existing.csv").read_text(encoding="utf-8") == "existing-report"
        scratch_db.write_text("new-db", encoding="utf-8")
        (scratch_output / "daily_pipeline_report_test.md").write_text("report", encoding="utf-8")
        return SimpleNamespace(
            report_path=scratch_output / "daily_pipeline_report_test.md",
            taiex_freshness_status="fresh",
            selected_orders=1,
            selected_orders_by_profile={"original_champion_tpex_500m": 1},
        )

    exit_code = runner.run_databricks_pipeline(make_args(root, scratch), pipeline_func=fake_pipeline)

    assert exit_code == 0
    assert persistent_db.read_text(encoding="utf-8") == "new-db"
    assert (root / "reports" / "live_signals" / "daily_pipeline_report_test.md").read_text(encoding="utf-8") == "report"
    assert (root / "logs" / "databricks_daily_pipeline_errors.log").exists() is False
    assert list((root / "logs").glob("databricks_daily_pipeline_*.log"))


def test_failure_does_not_overwrite_persistent_db_and_copies_logs(tmp_path: Path) -> None:
    runner = load_runner_module()
    root = tmp_path / "persistent"
    scratch = tmp_path / "scratch"
    persistent_db = root / "data" / "taiwan_trading.duckdb"
    persistent_db.parent.mkdir(parents=True)
    persistent_db.write_text("old-db", encoding="utf-8")

    def failing_pipeline(**kwargs):
        Path(kwargs["db_path"]).write_text("broken-db", encoding="utf-8")
        raise RuntimeError("simulated failure")

    exit_code = runner.run_databricks_pipeline(make_args(root, scratch), pipeline_func=failing_pipeline)

    assert exit_code == 1
    assert persistent_db.read_text(encoding="utf-8") == "old-db"
    assert (root / "logs" / "databricks_daily_pipeline_errors.log").read_text(encoding="utf-8").find(
        "simulated failure"
    ) >= 0
    assert list((root / "logs").glob("databricks_daily_pipeline_*.log"))


def test_local_mode_uses_root_local_scratch_by_default(tmp_path: Path) -> None:
    runner = load_runner_module()
    root = tmp_path / "local_root"
    persistent_db = root / "data" / "taiwan_trading.duckdb"
    persistent_db.parent.mkdir(parents=True)
    persistent_db.write_text("old-db", encoding="utf-8")

    def fake_pipeline(**kwargs):
        scratch_db = Path(kwargs["db_path"])
        assert root / ".databricks_scratch" in scratch_db.parents
        scratch_db.write_text("new-db", encoding="utf-8")
        return SimpleNamespace(
            report_path=Path(kwargs["output_dir"]) / "daily_pipeline_report_test.md",
            taiex_freshness_status="fresh",
            selected_orders=0,
            selected_orders_by_profile={},
        )

    exit_code = runner.run_databricks_pipeline(make_args(root, None), pipeline_func=fake_pipeline)

    assert exit_code == 0
    assert persistent_db.read_text(encoding="utf-8") == "new-db"


def make_args(root: Path, scratch_root: Path | None) -> SimpleNamespace:
    return SimpleNamespace(
        root=root,
        scratch_root=scratch_root,
        db=None,
        capital_twd=1_000_000.0,
        market="BOTH",
        profile="all",
        start=None,
        end="latest",
        signal_date="latest",
        skip_data_update=False,
        skip_index_update=False,
        skip_sector_update=False,
        refresh_sector_map=False,
        taiex_retry_delay_seconds=0.0,
        dry_run=False,
    )
