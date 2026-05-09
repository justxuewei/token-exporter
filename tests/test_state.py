import json
import os
import tempfile
from datetime import datetime, timezone

from watcher import JSONLWatcher


def make_claude_record(msg_id: str, request_id: str) -> str:
    """Create a Claude Code JSONL record."""
    return json.dumps({
        "message": {
            "id": msg_id,
            "model": "claude-3-5-sonnet-20241022",
            "usage": {
                "input_tokens": 800,
                "output_tokens": 200,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 50,
            }
        },
        "requestId": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }) + "\n"


def create_claude_jsonl(tmpdir: str, project_name: str, records: list[tuple[str, str]]) -> str:
    """Create a Claude Code JSONL file in the expected directory structure.

    Returns the path to the created file.
    """
    # Claude Code expects: <dir>/projects/<encoded-path>/<uuid>.jsonl
    projects_dir = os.path.join(tmpdir, "projects")
    project_dir = os.path.join(projects_dir, f"-home-nxw-developer-{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    jsonl_file = os.path.join(project_dir, "test-session.jsonl")
    with open(jsonl_file, "w") as f:
        for msg_id, req_id in records:
            f.write(make_claude_record(msg_id, req_id))

    return jsonl_file


class TestStatePersistence:
    def test_state_persistence(self):
        """Test that state is persisted and restored correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            create_claude_jsonl(tmpdir, "test-project", [
                ("msg1", "req1"),
                ("msg2", "req2"),
            ])

            records = []

            def on_record(agent, record):
                records.append(record)

            # First run: scan history
            watcher = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file=state_file,
                on_record=on_record,
            )
            watcher.scan_history()

            assert len(records) == 2
            assert len(watcher._seen_keys) == 2

            # Check state file was created
            assert os.path.exists(state_file)

            with open(state_file) as f:
                state = json.load(f)
            assert len(state["seen_keys"]) == 2

            # Second run: restart with same state file
            records.clear()
            watcher2 = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file=state_file,
                on_record=on_record,
            )
            watcher2.scan_history()

            # Prometheus counters are in-memory, so a restarted exporter must
            # replay history to rehydrate metrics even when state exists.
            assert len(records) == 2
            assert len(watcher2._seen_keys) == 2

    def test_state_file_not_required(self):
        """Test that watcher works without state_file (backwards compatible)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            create_claude_jsonl(tmpdir, "test-project", [("msg1", "req1")])

            records = []

            def on_record(agent, record):
                records.append(record)

            # No state_file - should work fine
            watcher = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file="",  # Empty - no state
                on_record=on_record,
            )
            watcher.scan_history()

            assert len(records) == 1

    def test_check_updates_uses_persisted_position(self):
        """Test that check_updates uses persisted file positions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            create_claude_jsonl(tmpdir, "test-project", [
                ("msg1", "req1"),
                ("msg2", "req2"),
            ])

            records = []

            def on_record(agent, record):
                records.append(record)

            # First run: scan history
            watcher = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file=state_file,
                on_record=on_record,
            )
            watcher.scan_history()
            assert len(records) == 2

            # Restart: load state and check_updates
            records.clear()
            watcher2 = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file=state_file,
                on_record=on_record,
            )
            watcher2.check_updates()

            # Should find 0 new records (file hasn't changed)
            assert len(records) == 0

            # Now append a new record
            projects_dir = os.path.join(tmpdir, "projects")
            project_dir = os.path.join(projects_dir, "-home-nxw-developer-test-project")
            jsonl_file = os.path.join(project_dir, "test-session.jsonl")
            with open(jsonl_file, "a") as f:
                f.write(make_claude_record("msg3", "req3"))

            # check_updates should find the new record
            records.clear()
            watcher2.check_updates()
            assert len(records) == 1

    def test_dedup_key_prevents_duplicates(self):
        """Test that dedup_key actually prevents duplicate counting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")

            # Create file with duplicate dedup keys
            create_claude_jsonl(tmpdir, "test-project", [
                ("msg1", "req1"),
                ("msg1", "req1"),  # duplicate
            ])

            records = []

            def on_record(agent, record):
                records.append(record)

            watcher = JSONLWatcher(
                claude_dirs=[tmpdir],
                days_back=7,
                state_file=state_file,
                on_record=on_record,
            )
            watcher.scan_history()

            # Should only count 1, not 2 (dedup)
            assert len(records) == 1
            assert len(watcher._seen_keys) == 1
