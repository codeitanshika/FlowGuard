"""Disposable local webhook receiver for verifying Grafana alerting fires
end-to-end (Phase 14) — logs every request it receives to stdout and
responds 200. Not a real notification channel: swap the contact point's
URL for Slack/PagerDuty/etc. in any real deployment. Stdlib only, no
dependencies, because its only job is to exist and be inspectable via
`docker compose logs alertsink`."""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        print(f"[alertsink] {self.path} <- {body}", flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - BaseHTTPRequestHandler's hook name
        pass  # the do_POST print above is the log; skip the default per-request line


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
