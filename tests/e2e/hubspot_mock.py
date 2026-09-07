"""Local HubSpot-compatible mock for the e2e rehearsal.

Implements exactly the endpoints pipeline/library_files/client.py calls, keeps
state in memory, and exposes it on /__state so the report can cross-check
what the pipeline believes (ledger) against what "HubSpot" received.

  python tests/e2e/hubspot_mock.py --port 8702 [--fail-once upload]

--fail-once upload : the first POST /files/v3/files answers 429 + Retry-After
                     so the uploader's retry path is exercised.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STATE = {
    "portalId": 424242,
    "companies": {"9001": {"id": "9001", "properties": {"name": "Thorlabs", "domain": "thorlabs.com"}}},
    "files": {}, "notes": {}, "deals": {}, "associations": [],
    "requests": {"search": 0, "company_create": 0, "upload": 0, "note": 0, "assoc": 0, "deal": 0, "delete_note": 0},
    "failed_once": False,
}
LOCK = threading.Lock()
SEQ = {"company": 1000, "file": 3000, "note": 7000, "deal": 5000}
FAIL_ONCE = None


def _next(kind: str) -> str:
    SEQ[kind] += 1
    return str(SEQ[kind])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _read(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json(self, code: int, payload=None, extra: dict | None = None) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization", "").startswith("Bearer ")

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/__health":
            return self._json(200, {"ok": True})
        if p == "/__state":
            with LOCK:
                return self._json(200, STATE)
        if not self._auth_ok():
            return self._json(401, {"message": "missing bearer"})
        if p == "/account-info/v3/details":
            return self._json(200, {"portalId": STATE["portalId"], "accountType": "SANDBOX", "timeZone": "Europe/Zurich"})
        return self._json(404, {"message": f"no route GET {p}"})

    def do_POST(self):
        p = urlparse(self.path).path
        raw = self._read()
        if p == "/__reset":
            return self._json(200, {"ok": True})
        if not self._auth_ok():
            return self._json(401, {"message": "missing bearer"})
        with LOCK:
            if p == "/crm/v3/objects/companies/search":
                STATE["requests"]["search"] += 1
                body = json.loads(raw or b"{}")
                name = body["filterGroups"][0]["filters"][0]["value"]
                hits = [c for c in STATE["companies"].values() if c["properties"]["name"] == name]
                return self._json(200, {"total": len(hits), "results": hits})
            if p == "/crm/v3/objects/companies":
                STATE["requests"]["company_create"] += 1
                props = json.loads(raw)["properties"]
                cid = _next("company")
                STATE["companies"][cid] = {"id": cid, "properties": props}
                return self._json(201, STATE["companies"][cid])
            if p == "/files/v3/files":
                STATE["requests"]["upload"] += 1
                if FAIL_ONCE == "upload" and not STATE["failed_once"]:
                    STATE["failed_once"] = True
                    return self._json(429, {"message": "rate limited (mock)"}, {"Retry-After": "0"})
                m = re.search(rb'filename="([^"]+)"', raw)
                fid = _next("file")
                STATE["files"][fid] = {"id": fid, "name": m.group(1).decode() if m else "?", "bytes": len(raw)}
                return self._json(201, {"id": fid, "name": STATE["files"][fid]["name"]})
            if p == "/crm/v3/objects/notes":
                STATE["requests"]["note"] += 1
                props = json.loads(raw)["properties"]
                if not props.get("hs_attachment_ids"):
                    return self._json(400, {"message": "hs_attachment_ids required (mock)"})
                nid = _next("note")
                STATE["notes"][nid] = {"id": nid, "properties": props}
                return self._json(201, STATE["notes"][nid])
            if p == "/crm/v3/objects/deals/search":
                return self._json(200, {"total": 0, "results": []})
            if p == "/crm/v3/objects/deals":
                STATE["requests"]["deal"] += 1
                props = json.loads(raw)["properties"]
                if not props.get("dealname") or not props.get("dealstage"):
                    return self._json(400, {"message": "dealname and dealstage required"})
                did = _next("deal")
                STATE["deals"][did] = {"id": did, "properties": props}
                return self._json(201, STATE["deals"][did])
        return self._json(404, {"message": f"no route POST {p}"})

    def do_PUT(self):
        p = urlparse(self.path).path
        self._read()
        if not self._auth_ok():
            return self._json(401, {"message": "missing bearer"})
        m = re.match(r"^/crm/v4/objects/(\w+)/(\w+)/associations/default/(\w+)/(\w+)$", p)
        if m:
            f_type, f_id, t_type, t_id = m.groups()
            with LOCK:
                STATE["requests"]["assoc"] += 1
                store = {"note": STATE["notes"], "deal": STATE["deals"], "company": STATE["companies"]}
                if f_id not in store.get(f_type, {}) or t_id not in store.get(t_type, {}):
                    return self._json(404, {"message": f"unknown object in {f_type}:{f_id} → {t_type}:{t_id}"})
                STATE["associations"].append({"from": f"{f_type}:{f_id}", "to": f"{t_type}:{t_id}"})
            return self._json(200, {"fromObjectTypeId": f_type, "toObjectTypeId": t_type})
        return self._json(404, {"message": f"no route PUT {p}"})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if not self._auth_ok():
            return self._json(401, {"message": "missing bearer"})
        m = re.match(r"^/crm/v3/objects/notes/(\w+)$", p)
        if m:
            with LOCK:
                STATE["requests"]["delete_note"] += 1
                STATE["notes"].pop(m.group(1), None)
            return self._json(204)
        return self._json(404, {"message": f"no route DELETE {p}"})


def main() -> None:
    global FAIL_ONCE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--fail-once", choices=["upload"], default=None)
    a = ap.parse_args()
    FAIL_ONCE = a.fail_once
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"hubspot-mock portal={STATE['portalId']} fail_once={FAIL_ONCE} on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
