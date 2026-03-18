"""
VAST 3.0 Ad Pod Server for CTV — pure Python stdlib, no dependencies.

Usage:
    python vast_server.py [port]      (default port: 8080)

Endpoints:
    GET /vast?pod_fill_secs=60                        → 6-ad pod (60s)
    GET /vast?pod_fill_secs=60&pod_fill_override_rnd=1 → random-length pod
    GET /health                                        → health check

Query-string params:
    pod_fill_secs           int  — total seconds to fill; divided by 10 gives
                                   the number of 10-second ads in the pod.
    pod_fill_override_rnd   0|1  — if 1, ignore pod_fill_secs and pick a random
                                   value from [0,10,20,30,40,50,60,70,80,90].
"""

import json
import random
import textwrap
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEDIA_URL        = "https://pub-a113c784a65f461da10e40b736a78647.r2.dev/10s.mp4"
MEDIA_TYPE       = "video/mp4"
BITRATE          = 2000          # kbps — medium quality for CTV
WIDTH            = 1920
HEIGHT           = 1080
AD_DURATION_SEC  = 10
AD_DURATION_FMT  = "00:00:10"

RANDOM_FILL_OPTIONS = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]


# ---------------------------------------------------------------------------
# VAST 3.0 XML builder
# ---------------------------------------------------------------------------

def build_ad_block(ad_index: int) -> str:
    """Return one <Ad> block (1-indexed sequence) for the pod."""
    ad_id = f"ad-{ad_index:03d}"
    cr_id = f"cr-{ad_index:03d}"
    mf_id = f"mf-{ad_index:03d}"

    return textwrap.dedent(f"""\
          <Ad id="{ad_id}" sequence="{ad_index}">
            <InLine>
              <AdSystem>PythonVASTServer</AdSystem>
              <AdTitle>Ad {ad_index}</AdTitle>
              <Impression id="imp-{ad_index:03d}"><![CDATA[]]></Impression>
              <Creatives>
                <Creative id="{cr_id}" sequence="1">
                  <Linear>
                    <Duration>{AD_DURATION_FMT}</Duration>
                    <MediaFiles>
                      <MediaFile
                        id="{mf_id}"
                        delivery="progressive"
                        type="{MEDIA_TYPE}"
                        bitrate="{BITRATE}"
                        width="{WIDTH}"
                        height="{HEIGHT}"
                      ><![CDATA[{MEDIA_URL}]]></MediaFile>
                    </MediaFiles>
                  </Linear>
                </Creative>
              </Creatives>
            </InLine>
          </Ad>""")


def build_vast_pod(ad_count: int) -> str:
    """Return a complete VAST 3.0 Ad Pod XML string."""
    if ad_count == 0:
        return '<?xml version="1.0" encoding="UTF-8"?>\n<VAST version="3.0"/>\n'

    ad_blocks = "\n".join(build_ad_block(i) for i in range(1, ad_count + 1))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<VAST version="3.0">\n'
        + ad_blocks + "\n"
        + "</VAST>\n"
    )


# ---------------------------------------------------------------------------
# Pod size logic
# ---------------------------------------------------------------------------

def resolve_ad_count(params: dict) -> int:
    """
    Determine how many 10-second ads to put in the pod.

    1. If pod_fill_override_rnd=1, pick a random value from RANDOM_FILL_OPTIONS.
    2. Otherwise use pod_fill_secs (defaults to 0 if missing / invalid).
    Divide the chosen seconds value by AD_DURATION_SEC to get the ad count.
    """
    use_random = params.get("pod_fill_override_rnd", "0").strip() == "1"

    if use_random:
        fill_secs = random.choice(RANDOM_FILL_OPTIONS)
    else:
        try:
            fill_secs = int(params.get("pod_fill_secs", 0))
        except ValueError:
            fill_secs = 0

    return fill_secs // AD_DURATION_SEC


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class VASTHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # silence default per-request logger
        pass

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        params = dict(urllib.parse.parse_qsl(parsed.query))

        routes = {
            "/vast":   self._handle_vast,
            "/health": self._handle_health,
        }

        handler = routes.get(path)
        if handler:
            handler(params)
        else:
            self._send_json(404, {"error": "Not found", "path": path})

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    def _handle_vast(self, params: dict):
        ad_count = resolve_ad_count(params)
        xml      = build_vast_pod(ad_count)
        self._log(
            f"VAST 3.0 Ad Pod — {ad_count} ad(s) "
            f"({ad_count * AD_DURATION_SEC}s) | "
            f"override_rnd={params.get('pod_fill_override_rnd', '0')} | "
            f"pod_fill_secs={params.get('pod_fill_secs', 'n/a')}"
        )
        self._send_xml(200, xml)

    def _handle_health(self, _params: dict):
        self._send_json(200, {
            "status": "ok",
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        })
        self._log("Health check")

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_xml(self, status: int, body: str):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log(self, msg: str):
        ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client = self.client_address[0]
        print(f"[{ts}] {client} — {self.command} {self.path}")
        print(f"         → {msg}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(host: str = "0.0.0.0", port: int = 8080):
    server = HTTPServer((host, port), VASTHandler)
    print(f"VAST 3.0 Ad Pod Server listening on http://{host}:{port}\n")
    print("  GET /vast?pod_fill_secs=60                         → 6-ad pod (60s)")
    print("  GET /vast?pod_fill_secs=60&pod_fill_override_rnd=1 → random-length pod")
    print("  GET /health                                        → health check")
    print("\nPress Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port=port)