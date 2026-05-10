import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import start_http_server

from config import load_config
from metrics import record_usage, set_source
from watcher import CcusageCollector, CodexCollector

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


def poll_loop(collectors: list, interval: int):
    for collector in collectors:
        collector.check_updates()
    timer = threading.Timer(interval, poll_loop, args=[collectors, interval])
    timer.daemon = True
    timer.start()


def main():
    config = load_config()
    logger.info("Config: %s", config)

    set_source(config["source"])

    collectors = []

    if config["claude_dirs"]:
        cc_state = config["state_file"].replace(".json", "-ccusage.json") if config["state_file"] else ""
        cc_collector = CcusageCollector(
            claude_dirs=config["claude_dirs"],
            days_back=config["days_back"],
            on_record=record_usage,
            state_file=cc_state,
            timezone_name=config["timezone"],
        )
        logger.info("Scanning Claude Code / AntCC history...")
        cc_collector.scan_history()
        logger.info("Claude Code / AntCC scan complete")
        collectors.append(cc_collector)

    if config["codex_dirs"]:
        codex_state = config["state_file"].replace(".json", "-codex.json") if config["state_file"] else ""
        codex_collector = CodexCollector(
            codex_dirs=config["codex_dirs"],
            days_back=config["days_back"],
            on_record=record_usage,
            state_file=codex_state,
            timezone_name=config["timezone"],
        )
        logger.info("Scanning Codex history...")
        codex_collector.scan_history()
        logger.info("Codex scan complete")
        collectors.append(codex_collector)

    start_http_server(config["listen_port"])
    logger.info("Prometheus metrics on :%d/metrics", config["listen_port"])

    health_port = config["listen_port"] + 1
    server = HTTPServer(("", health_port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health endpoint on :%d/health", health_port)

    logger.info("Watching for updates every %ds...", config["watch_interval"])
    poll_loop(collectors, config["watch_interval"])

    threading.Event().wait()


if __name__ == "__main__":
    main()