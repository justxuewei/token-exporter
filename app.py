import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import start_http_server

from config import load_config
from metrics import record_cc_blocks, record_codex_daily, record_usage, set_source
from rate_limit import fetch_cc_blocks, fetch_codex_daily
from watcher import JSONLWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("token-stats")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def poll_loop(watcher: JSONLWatcher, interval: int, ccusage_bin: str, ccusage_codex_bin: str):
    watcher.check_updates()
    _poll_rate_limits(ccusage_bin, ccusage_codex_bin)
    timer = threading.Timer(interval, poll_loop, args=[watcher, interval, ccusage_bin, ccusage_codex_bin])
    timer.daemon = True
    timer.start()


def _poll_rate_limits(ccusage_bin: str, ccusage_codex_bin: str):
    if ccusage_bin:
        blocks = fetch_cc_blocks(ccusage_bin)
        record_cc_blocks(blocks)
        logger.debug("CC blocks fetched: %d block(s)", len(blocks))
    if ccusage_codex_bin:
        daily = fetch_codex_daily(ccusage_codex_bin)
        record_codex_daily(daily)
        logger.debug("Codex daily rows fetched: %d row(s)", len(daily))


def main():
    config = load_config()
    logger.info("Config: %s", config)

    set_source(config["source"])

    watcher = JSONLWatcher(
        claude_dirs=config["claude_dirs"],
        days_back=config["days_back"],
        on_record=record_usage,
    )

    logger.info("Scanning historical data...")
    watcher.scan_history()
    logger.info("Historical scan complete")

    start_http_server(config["listen_port"])
    logger.info("Prometheus metrics on :%d/metrics", config["listen_port"])

    health_port = config["listen_port"] + 1
    server = HTTPServer(("", health_port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health endpoint on :%d/health", health_port)

    logger.info("Watching for new JSONL entries every %ds...", config["watch_interval"])
    poll_loop(watcher, config["watch_interval"], config["ccusage_bin"], config["ccusage_codex_bin"])

    threading.Event().wait()


if __name__ == "__main__":
    main()