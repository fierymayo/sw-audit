import json
import sys
import time
import urllib.parse
import urllib.request

import config


def get_admin_token(log=print):
    """ADMIN_TOKEN wins if set (legacy custom apps / permanent offline tokens).
    Otherwise mints a 24h token via the client credentials grant from
    CLIENT_ID + CLIENT_SECRET (Dev Dashboard apps), cached in output/.token.json."""
    if config.ADMIN_TOKEN:
        return config.ADMIN_TOKEN
    if not (config.CLIENT_ID and config.CLIENT_SECRET):
        sys.exit("Set ADMIN_TOKEN, or CLIENT_ID + CLIENT_SECRET, in .env (see .env.example).")
    import os
    try:
        with open(config.TOKEN_CACHE, encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("expires_at", 0) - time.time() > 300:
            return cached["access_token"]
    except (OSError, ValueError, KeyError):
        pass
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": config.CLIENT_ID,
        "client_secret": config.CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(
        f"https://{config.SHOP_DOMAIN}/admin/oauth/access_token", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if "shop_not_permitted" in detail:
            sys.exit("Client credentials grant refused (shop_not_permitted): the app and store "
                     "are not in the same Dev Dashboard organization. Run 'python get_token.py' "
                     "once to mint a permanent token via the authorization-code flow instead.")
        sys.exit(f"Token request failed ({e.code}): {detail}")
    token = data["access_token"]
    os.makedirs(config.OUT_DIR, exist_ok=True)
    with open(config.TOKEN_CACHE, "w", encoding="utf-8") as fh:
        json.dump({"access_token": token,
                   "expires_at": time.time() + data.get("expires_in", 86399)}, fh)
    log("  minted admin token via client credentials grant (valid 24h, cached).")
    return token


class ShopifyBulk:
    """Read-only bulk-operation client.

    Polls by op id via bulkOperation(id:) — currentBulkOperation is deprecated
    on 2026-07 and ambiguous now that 5 bulk queries can run concurrently.
    The token is never printed or logged.
    """

    def __init__(self, shop_domain, token, api_version):
        if not shop_domain:
            sys.exit("Missing SHOP_DOMAIN — set it in .env (see .env.example).")
        if not token:
            sys.exit("No admin token resolved — set ADMIN_TOKEN or CLIENT_ID + CLIENT_SECRET in .env.")
        self.endpoint = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self.token = token

    def _gql(self, query):
        body = json.dumps({"query": query}).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Shopify-Access-Token", self.token)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    def running(self):
        q = ('{ bulkOperations(first: 5, query: "(status:RUNNING OR status:CREATED) AND operation_type:QUERY")'
             ' { edges { node { id status } } } }')
        return [e["node"] for e in self._gql(q)["bulkOperations"]["edges"]]

    def get_op(self, op_id):
        d = self._gql(f'{{ bulkOperation(id: "{op_id}") {{ id status url errorCode objectCount }} }}')
        return d.get("bulkOperation")

    def kickoff(self, bulk_query, force=False, log=print):
        running = self.running()
        if running and not force:
            ids = ", ".join(f"{o['id']} ({o['status']})" for o in running)
            sys.exit(f"A bulk query op is already in flight: {ids}. "
                     f"Wait for it, or re-run with --force to cancel & restart.")
        for op in running:
            self._gql(f'mutation {{ bulkOperationCancel(id: "{op["id"]}") '
                      f'{{ userErrors {{ message }} }} }}')
        if running:
            time.sleep(3)
        q = bulk_query.replace("\\", "\\\\").replace('"""', '\\"""')
        m = f'''
        mutation {{
          bulkOperationRunQuery(query: """{q}""") {{
            bulkOperation {{ id status }}
            userErrors {{ field message }}
          }}
        }}'''
        d = self._gql(m)
        errs = d["bulkOperationRunQuery"]["userErrors"]
        if errs:
            raise RuntimeError(f"bulkOperationRunQuery userErrors: {errs}")
        op_id = d["bulkOperationRunQuery"]["bulkOperation"]["id"]
        log(f"  bulk op started: {op_id}")
        return op_id

    def wait(self, op_id, poll=5, timeout=3600, log=print):
        start = time.time()
        while True:
            cur = self.get_op(op_id)
            st = cur["status"] if cur else "UNKNOWN"
            log(f"  bulk op: {st}  objects={cur.get('objectCount') if cur else '-'}")
            if st == "COMPLETED":
                return cur
            if st in ("FAILED", "CANCELED", "EXPIRED"):
                raise RuntimeError(f"bulk op {st}: {cur.get('errorCode')}")
            if time.time() - start > timeout:
                raise TimeoutError("bulk op did not complete in time")
            time.sleep(poll)

    @staticmethod
    def download(url, path, log=print):
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, path)
        log(f"  downloaded {path}")
        return path

    def run_to_file(self, bulk_query, path, force=False, log=print):
        op_id = self.kickoff(bulk_query, force=force, log=log)
        cur = self.wait(op_id, log=log)
        url = cur.get("url")
        if not url:
            sys.exit("Bulk op completed with no result URL (empty result set?).")
        return self.download(url, path, log=log)
