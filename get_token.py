#!/usr/bin/env python3
"""One-time fallback when the client credentials grant fails with shop_not_permitted
(app and store in different Dev Dashboard organizations).

Runs the authorization-code flow locally and prints a permanent offline
ADMIN_TOKEN for .env. Before running, in the app version config add
    http://localhost:8737/callback
to "Allowed redirection URL(s)" and release the version. Then:
    python get_token.py
and approve in the browser window that opens (logged into the store admin).
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

import config

PORT = 8737
REDIRECT = f"http://localhost:{PORT}/callback"


def main():
    if not (config.SHOP_DOMAIN and config.CLIENT_ID and config.CLIENT_SECRET):
        sys.exit("Set SHOP_DOMAIN, CLIENT_ID and CLIENT_SECRET in .env first.")

    state = secrets.token_urlsafe(16)
    result = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            if ok:
                result["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h3>%s</h3>" % (b"Token captured - return to the terminal."
                                               if ok else b"State mismatch or no code - retry."))
            if ok:
                done.set()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    auth_url = (f"https://{config.SHOP_DOMAIN}/admin/oauth/authorize?"
                + urllib.parse.urlencode({"client_id": config.CLIENT_ID,
                                          "scope": "read_products",
                                          "redirect_uri": REDIRECT,
                                          "state": state}))
    print("Opening browser for approval (log in to the store admin if asked)...")
    print(f"If it doesn't open, visit:\n  {auth_url}")
    webbrowser.open(auth_url)

    if not done.wait(timeout=300):
        sys.exit("Timed out waiting for the redirect. Is the redirect URL released on the app version?")
    server.shutdown()

    body = json.dumps({"client_id": config.CLIENT_ID,
                       "client_secret": config.CLIENT_SECRET,
                       "code": result["code"]}).encode()
    req = urllib.request.Request(f"https://{config.SHOP_DOMAIN}/admin/oauth/access_token",
                                 data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    token = data.get("access_token")
    if not token:
        sys.exit(f"No access_token in response: {data}")
    print("\nPermanent offline token minted. Put this in .env:\n")
    print(f"ADMIN_TOKEN={token}")
    print(f"\n(granted scopes: {data.get('scope', '?')})")


if __name__ == "__main__":
    main()
