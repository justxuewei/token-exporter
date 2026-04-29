import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import start_http_server

from config import load_config
from metrics import record_usage, set_source
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


def poll_loop(watcher: JSONLWatcher, interval: int):
    watcher.check_updates()
    timer = threading.Timer(interval, poll_loop, args=[watcher, interval])
    timer.daemon = True
    timer.start()


def main():
    config = load_config()
    logger.info("Config: %s", config)

    set_source(config["source"])

    watcher = JSONLWatcher(
        claude_dirs=config["claude_dirs"],
        days_back=config["days_back"],
        state_file=config["state_file"],
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
    poll_loop(watcher, config["watch_interval"])

    threading.Event().wait()


if __name__ == "__main__":
    main()
