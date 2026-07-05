from __future__ import annotations

import importlib.util
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

