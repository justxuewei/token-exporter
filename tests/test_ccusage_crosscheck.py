"""Cross-check CcusageCollector results against raw ccusage CLI output.

Runs ccusage on the same data directory and verifies that CcusageCollector
produces matching token totals. Requires ccusage to be installed and
ANTCC_DATA_DIR to point at a real antcc data directory.
"""

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from watcher import CcusageCollector

ANTCC_DATA_DIR = os.environ.get("ANTCC_DATA_DIR", os.path.expanduser("~/.codefuse/engine/cc"))


def _ccusage_available():
    try:
        result = subprocess.run(["ccusage", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _antcc_data_available():
    projects_dir = os.path.join(ANTCC_DATA_DIR, "projects")
    return os.path.isdir(projects_dir)


skip_unless_integration = pytest.mark.skipif(
    not (_ccusage_available() and _antcc_data_available()),
    reason="ccusage not installed or ANTCC_DATA_DIR not found (set ANTCC_DATA_DIR to run)",
)


def _run_ccusage_daily(since: str = "", timezone_name: str = "UTC") -> dict:
    """Run ccusage daily --json and return parsed output."""
    cmd = ["ccusage", "daily", "--json", "--offline", "--timezone", timezone_name]
    env = {**os.environ, "CLAUDE_CONFIG_DIR": ANTCC_DATA_DIR}
    if since:
        cmd += ["--since", since]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"ccusage failed: {result.stderr}")
    return json.loads(result.stdout)


def _ccusage_model_totals(ccusage_data: dict) -> dict:
    """Extract per-model token totals from ccusage JSON output."""
    totals = {}
    for entry in ccusage_data.get("daily", []):
        for bd in entry.get("modelBreakdowns", []):
            model = bd.get("modelName", "unknown")
            if model not in totals:
                totals[model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                }
            t = totals[model]
            t["input_tokens"] += bd.get("inputTokens", 0)
            t["output_tokens"] += bd.get("outputTokens", 0)
            t["cache_creation_tokens"] += bd.get("cacheCreationTokens", 0)
            t["cache_read_tokens"] += bd.get("cacheReadTokens", 0)
    return totals


def _collector_model_totals(days_back: int) -> dict:
    """Run CcusageCollector and return totals grouped by model."""
    totals = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    })

    def on_record(agent, record):
        model = record["model"]
        t = totals[model]
        t["input_tokens"] += record["input_tokens"]
        t["output_tokens"] += record["output_tokens"]
        t["cache_creation_tokens"] += record["cache_creation_tokens"]
        t["cache_read_tokens"] += record["cache_read_tokens"]

    collector = CcusageCollector(
        claude_dirs=[ANTCC_DATA_DIR],
        days_back=days_back,
        on_record=on_record,
    )
    collector.scan_history()
    return dict(totals)


ZERO_TOTALS = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}


def _assert_totals_match(ccusage_totals: dict, collector_totals: dict, label: str = ""):
    """Assert that ccusage and collector totals match for all models."""
    all_models = set(ccusage_totals.keys()) | set(collector_totals.keys())
    if not all_models:
        pytest.skip("No usage data found")

    for model in sorted(all_models):
        cc = ccusage_totals.get(model, ZERO_TOTALS)
        ct = collector_totals.get(model, ZERO_TOTALS)
        for field in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"):
            assert cc[field] == ct[field], (
                f"{label}Model {model!r} {field} mismatch: ccusage={cc[field]} collector={ct[field]}"
            )


@skip_unless_integration
class TestCcusageCrosscheck:
    """Compare CcusageCollector totals against raw ccusage CLI output."""

    def test_totals_match_last_7_days(self):
        """Last 7 days token totals should match ccusage."""
        since = (datetime.now(timezone.utc).date() - timedelta(days=7)).strftime("%Y%m%d")

        ccusage_data = _run_ccusage_daily(since=since)
        collector_totals = _collector_model_totals(days_back=7)

        ccusage_totals = _ccusage_model_totals(ccusage_data)
        if not ccusage_totals:
            pytest.skip("No ccusage data for last 7 days")

        _assert_totals_match(ccusage_totals, collector_totals, label="7-day: ")