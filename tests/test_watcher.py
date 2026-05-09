import json
import tempfile
import unittest
from pathlib import Path

from watcher import JSONLWatcher


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


if __name__ == "__main__":
    unittest.main()
