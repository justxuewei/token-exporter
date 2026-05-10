from watcher import _detect_agent


class TestDetectAgent:
    def test_claude_code(self):
        assert _detect_agent("/home/user/.claude") == "cc"
        assert _detect_agent("/Users/me/.claude") == "cc"

    def test_antcc(self):
        assert _detect_agent("/home/user/.codefuse/engine/cc") == "antcc"

    def test_unknown(self):
        assert _detect_agent("/home/user/.codefuse/codefuse-cc") == "unknown"

    def test_antcc_codex(self):
        assert _detect_agent("/home/user/.codefuse/engine/codex") == "antcodex"

    def test_codex(self):
        assert _detect_agent("/home/user/.codex") == "codex"
        assert _detect_agent("/Users/me/.codex") == "codex"