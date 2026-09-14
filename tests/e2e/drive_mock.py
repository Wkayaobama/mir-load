"""Local Drive-v3-compatible mock for the e2e rehearsal.

Serves a tree modelled on the live Miraex Drive (scope root id identical to
the card, segment folders, company folders, PO/Billing/other PDFs, native
Google docs that must be exported, a shortcut, an orphan spreadsheet at
segment level, and a `70 Tradeshows` subtree that must be pruned).
The real google-api-python-client is pointed at it via MRLOAD_DRIVE_API_BASE.

  python tests/e2e/drive_mock.py --port 8701 [--scenario clean|dirty]

Endpoints: GET /drive/v3/files (q='<id>' in parents, pageToken, 3/page),
GET /drive/v3/files/<id>[?alt=media], GET /drive/v3/files/<id>/export,
GET /__state, GET /__health.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT_ID = "1If3SX0GD6FJypMy23na6HizFD6gciw_0"   # == context/cards/library.yaml scope root
FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
GSHEET = "application/vnd.google-apps.spreadsheet"
GSLIDES = "application/vnd.google-apps.presentation"
SHORTCUT = "application/vnd.google-apps.shortcut"
PDF = "application/pdf"
OWNER = {"emailAddress": "ops@miraex.example", "displayName": "Miraex Ops"}
PAGE_SIZE = 3


class Tree:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.content: dict[str, bytes] = {}
        self._n = 0

    def _id(self, hint: str) -> str:
        self._n += 1
        slug = re.sub(r"[^A-Za-z0-9]+", "", hint)[:12]
        return f"m{self._n:03d}{slug}"

    def add(self, name: str, parents: list[str], mime: str = PDF, *, content: bytes | None = None,
            created="2026-03-01T09:00:00.000Z", modified="2026-06-15T10:30:00.000Z",
            shortcut_target: str | None = None) -> str:
        fid = self._id(name)
        node = {
            "id": fid, "name": name, "mimeType": mime, "parents": parents,
            "createdTime": created, "modifiedTime": modified, "owners": [OWNER], "trashed": False,
            "webViewLink": (f"https://drive.google.com/drive/folders/{fid}" if mime == FOLDER
                            else f"https://drive.google.com/file/d/{fid}/view"),
        }
        if mime not in (FOLDER, GDOC, GSHEET, GSLIDES, SHORTCUT):
            body = content if content is not None else f"%MOCK {name}\n".encode() * 40
            node["size"] = str(len(body))
            node["md5Checksum"] = f"{abs(hash(body)) % (1 << 64):016x}"
            self.content[fid] = body
        if shortcut_target:
            node["shortcutDetails"] = {"targetId": shortcut_target}
        self.nodes[fid] = node
        return fid

    def children(self, parent: str) -> list[dict]:
        return [n for n in self.nodes.values() if parent in n["parents"] and not n["trashed"]]


def build_tree(scenario: str) -> Tree:
    t = Tree()
    quantum = t.add("Quantum", [ROOT_ID], FOLDER)
    photonics = t.add("Photonics", [ROOT_ID], FOLDER)
    trade = t.add("70 Tradeshows", [ROOT_ID], FOLDER)           # must be pruned
    t.add("booth_plan.pdf", [trade]); t.add("0 TradeShow toolKit", [trade], FOLDER)
    t.add("251226 Miraex Lead Opportunities.xlsx", [quantum],    # orphan at segment level
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    companies = {}
    for name in ("Thorlabs", "Toshiba", "Bluefors", "IQM", "Alice & Bob", "CERN"):
        companies[name] = t.add(name, [quantum], FOLDER)
    companies["Lionix"] = t.add("Lionix", [photonics], FOLDER)

    th = companies["Thorlabs"]
    t.add("PO_4711 Thorlabs.pdf", [th])                          # deal_candidate
    t.add("Quotation AN1 version 2.pdf", [th])                   # parked
    t.add("Design Manual v2.pdf", [th])                          # parked
    t.add("NDA Thorlabs.docx", [th], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    t.add("Meeting notes 13 July", [th], GDOC)                   # export → docx
    sub = t.add("Submissions", [th], FOLDER)
    t.add("Billing-2026-03.pdf", [sub])                          # deal_candidate at depth 4
    t.add("lnoi_MZI_tests.gds", [sub], "application/octet-stream")
    t.add("link to LN4 spec", [th], SHORTCUT, shortcut_target="elsewhere")

    to = companies["Toshiba"]
    t.add("Toshiba PO 2026-001.pdf", [to])                       # deal_candidate, directly under the company → self-anchored deal
    # ── deal layer fixtures (level 3 = first folder under the company) ──
    rfq = t.add("2026 Quantum sensor RFQ", [to], FOLDER)         # qualifies: PDFs beneath, name not excluded → ONE deal
    t.add("Quote QS-17.pdf", [rfq])                              # parked (quote) — carries the deal, deferred association
    t.add("PO 2026-042.pdf", [rfq])                              # deal_candidate → note associated to the deal
    t.add("SOW.docx", [rfq], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    draw = t.add("drawings", [rfq], FOLDER)                      # level 4: inherits the anchor
    t.add("layout.gds", [draw], "application/octet-stream")
    t.add("spec sheet.pdf", [to])                                # parked
    t.add("pricing.xlsx", [to], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    t.add("Roadmap", [to], GSLIDES)                              # export → pptx
    t.add("logo.png", [to], "image/png")

    t.add("cryostat quote.pdf", [companies["Bluefors"]])
    survey = t.add("Site survey", [companies["Bluefors"]], FOLDER)   # level 3 but NO pdf beneath → not a deal
    t.add("site photos.docx", [survey], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    expo = t.add("2025 Photonics West Exhibition", [companies["Alice & Bob"]], FOLDER)   # excluded realm → never a deal
    t.add("booth quote.pdf", [expo])
    t.add("Bluefors framework agreement.docx", [companies["Bluefors"]],
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    t.add("IQM Billing Q2.PDF", [companies["IQM"]])              # deal_candidate (case-insensitive ext)
    t.add("IQM intro deck", [companies["IQM"]], GSLIDES)
    t.add("A&B report.pdf", [companies["Alice & Bob"]])
    t.add("CERN tender.pdf", [companies["CERN"]])
    t.add("CERN lead sheet", [companies["CERN"]], GSHEET)        # export → xlsx
    t.add("Lionix PDK notes.docx", [companies["Lionix"]],
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    if scenario == "dirty":
        t.add("shared datasheet.pdf", [th, to])                  # multi-parent → REJECT
        dup = t.add("Toshiba", [quantum], FOLDER)                # duplicate company name → STOP
        t.add("dup file.pdf", [dup])
    return t


class Handler(BaseHTTPRequestHandler):
    tree: Tree
    state: dict = {"requests": 0, "list_calls": 0, "media_calls": 0, "export_calls": 0}
    lock = threading.Lock()

    def log_message(self, *a):  # quiet
        pass

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with self.lock:
            self.state["requests"] += 1
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/__health":
            return self._json(200, {"ok": True})
        if u.path == "/__state":
            return self._json(200, {**self.state, "nodes": len(self.tree.nodes)})
        if u.path == "/drive/v3/files":
            with self.lock:
                self.state["list_calls"] += 1
            q = qs.get("q", [""])[0]
            m = re.search(r"'([^']+)' in parents", q)
            if not m:
                return self._json(400, {"error": {"message": "unsupported q"}})
            items = sorted(self.tree.children(m.group(1)), key=lambda n: n["id"])
            start = int(qs.get("pageToken", ["0"])[0])
            page = items[start:start + PAGE_SIZE]
            out = {"files": page}
            if start + PAGE_SIZE < len(items):
                out["nextPageToken"] = str(start + PAGE_SIZE)
            return self._json(200, out)
        m = re.match(r"^/drive/v3/files/([^/]+)/export$", u.path)
        if m:
            with self.lock:
                self.state["export_calls"] += 1
            node = self.tree.nodes.get(m.group(1))
            if not node:
                return self._json(404, {"error": {"message": "not found"}})
            mime = qs.get("mimeType", ["application/octet-stream"])[0]
            return self._bytes(f"%EXPORT {node['name']} as {mime}\n".encode() * 20, mime)
        m = re.match(r"^/drive/v3/files/([^/]+)$", u.path)
        if m:
            node = self.tree.nodes.get(m.group(1))
            if not node:
                return self._json(404, {"error": {"message": "not found"}})
            if qs.get("alt", [""])[0] == "media":
                with self.lock:
                    self.state["media_calls"] += 1
                return self._bytes(self.tree.content.get(node["id"], b""), "application/octet-stream")
            return self._json(200, node)
        return self._json(404, {"error": {"message": f"no route {u.path}"}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--scenario", choices=["clean", "dirty"], default="clean")
    a = ap.parse_args()
    Handler.tree = build_tree(a.scenario)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"drive-mock scenario={a.scenario} nodes={len(Handler.tree.nodes)} on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
