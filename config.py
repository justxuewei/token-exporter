import os


def load_config():
    default_paths = os.path.expanduser("~/.claude,~/.codefuse/engine/cc,~/.codefuse/engine/codex,~/.codex")
    claude_dirs = os.environ.get("CLAUDE_CONFIG_DIR", default_paths)
    paths = [os.path.expanduser(p.strip()) for p in claude_dirs.split(",") if p.strip()]

    return {
        "listen_port": int(os.environ.get("LISTEN_PORT", "14531")),
        "watch_interval": int(os.environ.get("WATCH_INTERVAL", "5")),
        "claude_dirs": paths,
        "days_back": int(os.environ.get("DAYS_BACK", "7")),
        "source": os.environ.get("SOURCE", ""),
        "state_file": os.path.expanduser(os.environ.get("STATE_FILE", "~/.token-exporter/state.json")),
    }
