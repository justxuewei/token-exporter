import json
import os
import tempfile
import unittest
from pathlib import Path

from watcher import JSONLWatcher, parse_line


class CodexUsageTests(unittest.TestCase):
    def test_codex_reasoning_tokens_are_not_added_to_output(self):
        records = []
        lines = [
            {
                "timestamp": "2026-05-08T10:00:00Z",
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5-codex",
                    "cwd": "/home/nxw/developer/token-exporter",
                },
            },
            {
                "timestamp": "2026-05-08T10:00:01Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 15,
                        },
                        "last_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 15,
                        },
                    },
                },
            },
            {
                "timestamp": "2026-05-08T10:01:01Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 175,
                            "cached_input_tokens": 60,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 35,
                        },
                        "last_token_usage": {
                            "input_tokens": 75,
                            "cached_input_tokens": 20,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 20,
                        },
                    },
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            session = Path(tmpdir) / "session.jsonl"
            session.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

            watcher = JSONLWatcher([], on_record=lambda agent, rec: records.append((agent, rec)))
            _, count = watcher._read_codex_file(str(session), "codex", "unknown", None)

        self.assertEqual(count, 2)
        self.assertEqual([agent for agent, _ in records], ["codex", "codex"])
        self.assertEqual(records[0][1]["input_tokens"], 60)
        self.assertEqual(records[0][1]["cache_read_tokens"], 40)
        self.assertEqual(records[0][1]["output_tokens"], 20)
        self.assertEqual(records[1][1]["input_tokens"], 55)
        self.assertEqual(records[1][1]["cache_read_tokens"], 20)
        self.assertEqual(records[1][1]["output_tokens"], 30)

    def test_codex_cached_input_is_clamped_to_input(self):
        records = []
        lines = [
            {
                "timestamp": "2026-05-08T10:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 25,
                            "output_tokens": 5,
                        },
                    },
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            session = Path(tmpdir) / "session.jsonl"
            session.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

            watcher = JSONLWatcher([], on_record=lambda agent, rec: records.append((agent, rec)))
            _, count = watcher._read_codex_file(str(session), "codex", "unknown", None)

        self.assertEqual(count, 1)
        self.assertEqual(records[0][1]["input_tokens"], 0)
        self.assertEqual(records[0][1]["cache_read_tokens"], 10)
        self.assertEqual(records[0][1]["output_tokens"], 5)

    def test_codex_total_fallback_does_not_recount_counter_decrease(self):
        records = []
        lines = [
            {
                "timestamp": "2026-05-08T10:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 40,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 10,
                        },
                    },
                },
            },
            {
                "timestamp": "2026-05-08T10:01:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 50,
                            "cached_input_tokens": 20,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 5,
                        },
                    },
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            session = Path(tmpdir) / "session.jsonl"
            session.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

            watcher = JSONLWatcher([], on_record=lambda agent, rec: records.append((agent, rec)))
            _, count = watcher._read_codex_file(str(session), "codex", "unknown", None)

        self.assertEqual(count, 1)
        self.assertEqual(records[0][1]["input_tokens"], 60)
        self.assertEqual(records[0][1]["cache_read_tokens"], 40)
        self.assertEqual(records[0][1]["output_tokens"], 20)


class ClaudeUsageTests(unittest.TestCase):
    def test_claude_cache_only_usage_is_counted(self):
        record = parse_line(json.dumps({
            "timestamp": "2026-05-08T10:00:00Z",
            "message": {
                "id": "msg1",
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 12,
                    "cache_read_input_tokens": 34,
                },
            },
            "requestId": "req1",
        }))

        self.assertIsNotNone(record)
        self.assertEqual(record["input_tokens"], 0)
        self.assertEqual(record["output_tokens"], 0)
        self.assertEqual(record["cache_creation_tokens"], 12)
        self.assertEqual(record["cache_read_tokens"], 34)

    def test_claude_dedup_key_requires_message_and_request_id(self):
        missing_request = parse_line(json.dumps({
            "timestamp": "2026-05-08T10:00:00Z",
            "message": {
                "id": "msg1",
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
        }))
        missing_message = parse_line(json.dumps({
            "timestamp": "2026-05-08T10:00:00Z",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
            "requestId": "req1",
        }))
        complete = parse_line(json.dumps({
            "timestamp": "2026-05-08T10:00:00Z",
            "message": {
                "id": "msg1",
                "model": "claude-sonnet-4-20250514",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                },
            },
            "requestId": "req1",
        }))

        self.assertIsNone(missing_request["dedup_key"])
        self.assertIsNone(missing_message["dedup_key"])
        self.assertEqual(complete["dedup_key"], "msg1:req1")


class WatcherStateTests(unittest.TestCase):
    def test_scan_history_rehydrates_metrics_when_state_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.json")
            project_dir = Path(tmpdir) / "projects" / "-home-nxw-developer-token-exporter"
            project_dir.mkdir(parents=True)
            session = project_dir / "session.jsonl"
            session.write_text(json.dumps({
                "timestamp": "2026-05-08T10:00:00Z",
                "message": {
                    "id": "msg1",
                    "model": "claude-sonnet-4-20250514",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                    },
                },
                "requestId": "req1",
            }) + "\n")

            first_records = []
            watcher = JSONLWatcher(
                claude_dirs=[tmpdir],
                state_file=state_file,
                on_record=lambda agent, rec: first_records.append((agent, rec)),
            )
            watcher.scan_history()

            second_records = []
            restarted = JSONLWatcher(
                claude_dirs=[tmpdir],
                state_file=state_file,
                on_record=lambda agent, rec: second_records.append((agent, rec)),
            )
            restarted.scan_history()

        self.assertEqual(len(first_records), 1)
        self.assertEqual(len(second_records), 1)
        self.assertEqual(second_records[0][1]["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
