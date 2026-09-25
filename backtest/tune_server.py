"""Local tuning page: each node's decision flow (from docs/tv-ta) with its adjustable numbers as inputs
in the diagram; edit weights and thresholds in the browser, backtest them on the Binance
daily Up/Down questions, and copy the result as a ~/.tv-ta config patch.

  python backtest/tune_server.py            # http://127.0.0.1:8765/ opens in the browser
  python backtest/tune_server.py --port 9000 --no-open

Needs the question dataset first: python backtest/updown_dataset.py BTC ETH
The server listens on 127.0.0.1 only and never writes your config; "copy TOML" only returns text.
The backtest asks the question at each question's start, with candles closed by then, and scores the
sign of the average 1h / 4h / 1d composite against the settled result (see replay.py --updown).
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import skill_path  # noqa: F401  (puts skills/tv-ta/scripts on sys.path)

import fetch_ohlcv  # noqa: E402
import tune_core  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tune.html")
MAX_BODY = 64 * 1024
BACKTESTER = tune_core.Backtester()
RUN_LOCK = threading.Lock()


def make_handler(port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "tv-ta-tune"

        def log_message(self, fmt, *args):  # quiet: one line per API call is enough
            if self.path.startswith("/api/"):
                sys.stderr.write(f"{self.command} {self.path.split('?')[0]}\n")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            # a page on another site must not be able to drive this server (DNS rebinding)
            return self.headers.get("Host", "") in allowed_hosts

        def _symbol(self, raw) -> str:
            symbol = str(raw or "").upper()
            if symbol not in tune_core.available_symbols():
                raise tune_core.PatchError(f"no dataset for {symbol!r}; run backtest/updown_dataset.py first")
            return symbol

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            url = urlparse(self.path)
            if url.path == "/":
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if url.path == "/api/config":
                try:
                    symbols = tune_core.available_symbols()
                    symbol = self._symbol(parse_qs(url.query).get("symbol", [symbols[0] if symbols else ""])[0])
                    return self._json(200, {**tune_core.describe(symbol), "symbols": symbols})
                except tune_core.PatchError as e:
                    return self._json(400, {"error": str(e)})
            if url.path == "/api/flows":
                return self._json(200, tune_core.flow_sources())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            path = urlparse(self.path).path
            if path not in ("/api/backtest", "/api/toml"):
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise tune_core.PatchError("request body missing or too large")
                body = json.loads(self.rfile.read(length))
                symbol = self._symbol(body.get("symbol"))
                patch = body.get("patch") or {}
                if path == "/api/toml":
                    nodes, profile = tune_core.baseline(symbol)
                    changes = tune_core.prune_patch(patch, nodes, profile)
                    tune_core.apply_patch(nodes, profile, changes)  # validate before printing
                    return self._send(200, tune_core.to_toml(symbol, changes, profile).encode("utf-8"),
                                      "text/plain; charset=utf-8")
                days = body.get("days", 200)
                if not isinstance(days, int) or isinstance(days, bool):
                    raise tune_core.PatchError("days must be a whole number")
                if not RUN_LOCK.acquire(blocking=False):
                    return self._json(409, {"error": "another backtest is still running"})
                try:
                    return self._json(200, BACKTESTER.run(symbol, days, patch))
                finally:
                    RUN_LOCK.release()
            except (tune_core.PatchError, json.JSONDecodeError, UnicodeDecodeError) as e:
                self._json(400, {"error": str(e)})
            except fetch_ohlcv.FetchError as e:
                self._json(502, {"error": f"market data error: {e}"})

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Local node tuning and backtest page.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)
    if not tune_core.available_symbols():
        print("no dataset found; run: python backtest/updown_dataset.py BTC ETH", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.port))
    url = f"http://127.0.0.1:{args.port}/"
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"調參頁面：{url}（Ctrl+C 結束）")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
