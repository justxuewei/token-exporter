import os


def load_config():
    default_paths = os.path.expanduser("~/.claude,~/.codefuse/engine/cc,~/.codex")
    claude_dirs = os.environ.get("CLAUDE_CONFIG_DIR", default_paths)
    paths = [os.path.expanduser(p.strip()) for p in claude_dirs.split(",") if p.strip()]

    return {
        "listen_port": int(os.environ.get("LISTEN_PORT", "14531")),
        "watch_interval": int(os.environ.get("WATCH_INTERVAL", "5")),
        "claude_dirs": paths,
        "days_back": int(os.environ.get("DAYS_BACK", "7")),
        "source": os.environ.get("SOURCE", ""),
        "ccusage_bin": os.environ.get("CCUSAGE_BIN", ""),
        "ccusage_codex_bin": os.environ.get("CCUSAGE_CODEX_BIN", ""),
        # Plan / rate-limit configuration
        "cc_plan": os.environ.get("CC_PLAN", ""),
        "cc_block_limit_tokens": int(os.environ.get("CC_BLOCK_LIMIT_TOKENS", "0")),
        "cc_week_limit_tokens": int(os.environ.get("CC_WEEK_LIMIT_TOKENS", "0")),
        "codex_plan": os.environ.get("CODEX_PLAN", ""),
        "codex_block_limit_tokens": int(os.environ.get("CODEX_BLOCK_LIMIT_TOKENS", "0")),
        "codex_week_limit_tokens": int(os.environ.get("CODEX_WEEK_LIMIT_TOKENS", "0")),
    }