import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import config

BULKOP_STATE = os.path.join(config.CACHE_DIR, ".bulkops.json")


def get_admin_token(log=print):
    """ADMIN_TOKEN wins if set (legacy custom apps / permanent offline tokens).
    Otherwise mints a 24h token via the client credentials grant from
    CLIENT_ID + CLIENT_SECRET (Dev Dashboard apps), cached in output/.token.json."""
    if config.ADMIN_TOKEN:
        return config.ADMIN_TOKEN
    if not (config.CLIENT_ID and config.CLIENT_SECRET):
        sys.exit("Set ADMIN_TOKEN, or CLIENT_ID + CLIENT_SECRET, in .env (see .env.example).")
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
    os.makedirs(config.cache_dir(), exist_ok=True)
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

    Network resilience:
    - _gql retries transient network errors (timeouts, connection resets,
      429/5xx) with backoff; auth/client errors fail fast.
    - wait() tolerates poll outages — the bulk op keeps running server-side,
      so a dead poll is never a reason to abandon the op.
    - run_to_file() records the op id per query hash in output/.bulkops.json;
      a re-run within 2h resumes a RUNNING op or downloads a COMPLETED
      result directly instead of kicking off a duplicate.
    """

    def __init__(self, shop_domain, token, api_version):
        if not shop_domain:
            sys.exit("Missing SHOP_DOMAIN — set it in .env (see .env.example).")
        if not token:
            sys.exit("No admin token resolved — set ADMIN_TOKEN or CLIENT_ID + CLIENT_SECRET in .env.")
        self.endpoint = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"
        self.token = token

    def _gql(self, query, attempts=4):
        body = json.dumps({"query": query}).encode()
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(self.endpoint, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Shopify-Access-Token", self.token)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if attempt < attempts and (e.code == 429 or e.code >= 500):
                    time.sleep(min(5 * attempt, 20))
                    continue
                raise
            except (urllib.error.URLError, OSError) as e:
                if attempt < attempts:
                    print(f"  network hiccup ({e.__class__.__name__}), retry {attempt}/{attempts - 1}...")
                    time.sleep(min(5 * attempt, 20))
                    continue
                raise
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
            try:
                cur = self.get_op(op_id)
            except (urllib.error.URLError, OSError) as e:
                if time.time() - start > timeout:
                    raise
                log(f"  poll failed ({e.__class__.__name__}) — op keeps running server-side, retrying...")
                time.sleep(poll)
                continue
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
    def download(url, path, log=print, attempts=3):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".part"
        for attempt in range(1, attempts + 1):
            try:
                urllib.request.urlretrieve(url, tmp)
                os.replace(tmp, path)
                log(f"  downloaded {path}")
                return path
            except (urllib.error.URLError, OSError):
                if attempt == attempts:
                    raise
                log(f"  download failed, retrying ({attempt}/{attempts - 1})...")
                time.sleep(5 * attempt)

    def _state_load(self):
        try:
            with open(BULKOP_STATE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _state_save(self, state):
        os.makedirs(config.cache_dir(), exist_ok=True)
        with open(BULKOP_STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    def run_to_file(self, bulk_query, path, force=False, log=print):
        key = hashlib.sha256(bulk_query.encode()).hexdigest()[:16]
        state = self._state_load()
        entry = None if force else state.get(key)
        op_id = None
        if entry and time.time() - entry.get("started", 0) < 7200:
            cur = self.get_op(entry["op_id"])
            st = cur["status"] if cur else None
            if st in ("RUNNING", "CREATED"):
                log(f"  resuming bulk op {entry['op_id']} ({st})")
                op_id = entry["op_id"]
            elif st == "COMPLETED" and cur.get("url"):
                log(f"  reusing completed bulk op {entry['op_id']} from this session")
                out = self.download(cur["url"], path, log=log)
                state.pop(key, None)
                self._state_save(state)
                return out
        if not op_id:
            op_id = self.kickoff(bulk_query, force=force, log=log)
            state[key] = {"op_id": op_id, "started": time.time()}
            self._state_save(state)
        cur = self.wait(op_id, log=log)
        url = cur.get("url")
        if not url:
            sys.exit("Bulk op completed with no result URL (empty result set?).")
        out = self.download(url, path, log=log)
        state.pop(key, None)
        self._state_save(state)
        return out