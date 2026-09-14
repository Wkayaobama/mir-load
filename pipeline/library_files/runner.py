"""Standalone CLI entry point for the mr-load library_files module.

Sub-commands (pass 1, 30 Sales domain — see context/cards/library.yaml):
  walk       — Drive API depth-first walk of a scope root → hierarchy CSV
               (bronze) and optional offline silver preview.
  index      — Same emission from an rclone lsjson manifest (offline fallback).
  bq-load    — Load the hierarchy CSV into BigQuery (dbt builds silver).
  companies  — Resolve company folders to HubSpot companies (search, then
               create behind MRLOAD_APPROVE_COMPANY_CREATE).
  attach     — Upload every asset under a resolved company and attach it as
               a note on that company (two gates, dry-run default).
  unmigrate  — Roll back attached notes using the ledger as the index.
  review-export — Operator queues: deal candidates, parked PDFs, orphans, and
               the deal_decisions.csv template for pass 2.
  ledger-export — Step 6: dump the ledger tables to CSV and load them into
               BigQuery so dbt can join the HubSpot ids into silver.
  deals      — Pass 2: create deals from the approved decisions file, associate
               deal → company and note → deal (MRLOAD_APPROVE_DEAL_CREATE).

Approval gates mirror ic-load: an env var must be exactly "1" to go live.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .card import DEFAULT_CARD_PATH, load_library_card
from .companies import company_folders_from_hierarchy, resolve_companies
from .deal_anchors import deal_documents, qualify_deal_anchors
from .deals import read_decisions, resolve_deals, write_decisions_template
from .config import Settings
from .drive_walker import ApiDriveLister, DriveFile, dfs_entries
from .hierarchy import HierarchyWriter, read_hierarchy_csv
from .ledger import LEDGER_TABLES, SqliteLedger
from .manifest import load_manifest
from .properties import (OK_STATUSES, ensure_properties, load_catalog, load_property_plan, summarize,
                         verify_properties, write_mapping_sheet)
from .silver_library import SilverIndexBuilder
from .uploader import HubSpotFileUploader, LibraryFileRow
from .walker import IMAGE_EXTS, DriveTreeWalker

APPROVE_BQ_LOAD_ENV = "MRLOAD_APPROVE_BQ_LOAD"
APPROVE_COMPANY_CREATE_ENV = "MRLOAD_APPROVE_COMPANY_CREATE"
APPROVE_FILES_UPLOAD_ENV = "MRLOAD_APPROVE_FILES_UPLOAD"
APPROVE_FILE_NOTES_POST_ENV = "MRLOAD_APPROVE_FILE_NOTES_POST"
APPROVE_UNMIGRATE_ENV = "MRLOAD_APPROVE_UNMIGRATE"
APPROVE_DEAL_CREATE_ENV = "MRLOAD_APPROVE_DEAL_CREATE"
APPROVE_PROPERTY_CREATE_ENV = "MRLOAD_APPROVE_PROPERTY_CREATE"


def _gate(env: str) -> bool:
    return os.environ.get(env, "").strip() == "1"


def _banner(label: str, gates: list[tuple[str, str]]) -> None:
    print(f"library_files runner — {label} gates:", file=sys.stderr)
    for name, env in gates:
        live = _gate(env)
        print(f"  {name}: {'LIVE' if live else 'DRY'}   ({env}={'1' if live else 'unset'})", file=sys.stderr)
    print(file=sys.stderr)


def _walker_from_args(args: argparse.Namespace, card) -> DriveTreeWalker:
    return DriveTreeWalker(
        path_prefix=args.path_prefix,
        root_name=args.root_name,
        segment_depth=args.segment_depth,
        exclude_exts=IMAGE_EXTS if args.exclude_images else (),
        card=card,
    )


def _emit(entries, args: argparse.Namespace, card) -> int:
    entries = list(entries)
    writer = HierarchyWriter(_walker_from_args(args, card), id_scheme=args.id_scheme)
    out = Path(args.hierarchy_out).resolve()
    stats = writer.write_csv(entries, out)
    result: dict = {"hierarchy_out": str(out), "stats": stats.__dict__}
    if args.silver_preview_out:
        builder = SilverIndexBuilder(
            _walker_from_args(args, card),
            owner_prefix=args.owner_prefix,
            owner_email=args.owner_email,
            owner_fullname=args.owner_fullname,
            id_scheme=args.id_scheme,
            require_anchor=args.require_anchor,
        )
        sp = Path(args.silver_preview_out).resolve()
        result["silver_preview"] = {"out": str(sp), "stats": builder.write_csv(entries, sp).__dict__}
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0 if stats.total_nodes else 1


def cmd_walk(args: argparse.Namespace) -> int:
    card = load_library_card(Path(args.card) if args.card else None)
    root = card.root(args.root) if args.root else (card.roots[0] if card.roots else None)
    if root is None:
        print("no scope root: pass --root <name|drive_id> or declare one in the card", file=sys.stderr)
        return 2
    if not root.drive_id:
        print(f"root {root.name!r} has no drive_id in the card", file=sys.stderr)
        return 2
    args.root_name = args.root_name or root.name
    args.path_prefix = args.path_prefix if args.path_prefix is not None else root.path_prefix
    args.segment_depth = args.segment_depth or root.segment_depth
    settings = Settings.from_env()
    lister = ApiDriveLister.from_credentials(args.credentials or settings.google_credentials)
    entries = dfs_entries(lister, root.drive_id, card=card, max_depth=args.max_depth)
    return _emit(entries, args, card)


def cmd_index(args: argparse.Namespace) -> int:
    card = load_library_card(Path(args.card) if args.card else None)
    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    args.path_prefix = args.path_prefix or ""
    args.segment_depth = args.segment_depth or 1
    return _emit(load_manifest(manifest_path), args, card)


def cmd_bq_load(args: argparse.Namespace) -> int:
    _banner("bq-load", [("hierarchy load", APPROVE_BQ_LOAD_ENV)])
    schema_path = Path(__file__).parent / "sql" / "library_hierarchy.schema.json"
    cmd = [
        "bq", "load", "--source_format=CSV", "--skip_leading_rows=1", "--allow_quoted_newlines",
        "--replace" if args.replace else "--noreplace",
        f"{args.dataset}.{args.table}", args.source_uri, str(schema_path),
    ]
    print(" ".join(shlex.quote(c) for c in cmd))
    if not _gate(APPROVE_BQ_LOAD_ENV):
        return 0
    return subprocess.run(cmd).returncode


def _ledger(settings: Settings, override: str | None) -> SqliteLedger:
    ledger = SqliteLedger(Path(override) if override else settings.ledger_path)
    ledger.bootstrap()
    return ledger


def _client_or_none(settings: Settings):
    if not settings.hubspot_token:
        return None
    from .client import HubSpotClient

    return HubSpotClient.from_settings(settings)


def cmd_companies(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _banner("companies", [("company create", APPROVE_COMPANY_CREATE_ENV)])
    rows = read_hierarchy_csv(Path(args.hierarchy))
    folders = company_folders_from_hierarchy(rows)
    ledger = _ledger(settings, args.ledger)
    results = resolve_companies(
        folders, client=_client_or_none(settings), ledger=ledger,
        live_create=_gate(APPROVE_COMPANY_CREATE_ENV),
    )
    json.dump(results, sys.stdout, indent=2)
    print()
    return 1 if any(r["status"] in ("failed", "ambiguous_match") for r in results) else 0


def _rows_for_attach(hierarchy_rows: list[dict], company_map: dict[str, str], classes: set[str]) -> list[LibraryFileRow]:
    out: list[LibraryFileRow] = []
    for r in hierarchy_rows:
        if r.get("is_dir") in ("True", "true", "1"):
            continue
        if r.get("asset_class") not in classes:
            continue
        hs_company = company_map.get(r.get("company_node_key") or "")
        if not hs_company:
            continue
        drive_file = DriveFile(id=r["drive_id"], name=r["node_name"], mime_type=r.get("drive_mimetype") or "")
        out.append(
            LibraryFileRow(
                legacy_id=r["legacy_library_id"],
                file_name=r["node_name"],
                note_body=f"{r.get('legacy_file_path')}/{r['node_name']}\n{r.get('link') or ''}".strip(),
                target_associations=[("company", hs_company)],
                drive_file=drive_file,
            )
        )
    return out


def cmd_attach(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _banner("attach", [
        ("Phase 1 (file upload)", APPROVE_FILES_UPLOAD_ENV),
        ("Phase 2 (note + assoc)", APPROVE_FILE_NOTES_POST_ENV),
    ])
    upload_live, attach_live = _gate(APPROVE_FILES_UPLOAD_ENV), _gate(APPROVE_FILE_NOTES_POST_ENV)
    ledger = _ledger(settings, args.ledger)
    hierarchy_rows = read_hierarchy_csv(Path(args.hierarchy))
    company_map = ledger.company_map()
    classes = {c.strip() for c in args.include_classes.split(",") if c.strip()}
    rows = _rows_for_attach(hierarchy_rows, company_map, classes)
    if not rows:
        print("no attachable rows (no resolved companies, or classes filtered everything)", file=sys.stderr)
        return 1
    client = _client_or_none(settings)
    if client is None:
        if upload_live or attach_live:
            print("HUBSPOT_SANDBOX_TOKEN not set but a live gate is open", file=sys.stderr)
            return 2
        json.dump([{"legacy_id": r.legacy_id, "file_name": r.file_name, "targets": r.target_associations,
                    "status": "dry_run"} for r in rows], sys.stdout, indent=2)
        print()
        return 0
    lister = None
    if upload_live:
        lister = ApiDriveLister.from_credentials(args.credentials or settings.google_credentials)
    uploader = HubSpotFileUploader(client, lister=lister, cache_dir=Path(args.cache_dir or settings.cache_dir), ledger=ledger)
    result = uploader.upload_phase(rows, live=upload_live)
    result = uploader.attach_phase(rows, result, live=attach_live)
    json.dump(result, sys.stdout, indent=2, default=str)
    print()
    return 1 if any(e["status"] in ("failed", "partial") for e in result) else 0


def cmd_unmigrate(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _banner("unmigrate", [("delete attached notes", APPROVE_UNMIGRATE_ENV)])
    live = _gate(APPROVE_UNMIGRATE_ENV)
    ledger = _ledger(settings, args.ledger)
    rows = ledger.load_attached_rows()
    if not rows:
        json.dump([], sys.stdout)
        print()
        return 0
    client = _client_or_none(settings) if live else None
    results = []
    for entry in rows:
        lid, nid = entry["legacy_library_id"], entry["hs_note_id"]
        if not live:
            results.append({"legacy_id": lid, "hs_note_id": nid, "status": "would_unattach", "error": None})
            continue
        try:
            client.delete_note(nid)
            status, error = "unattached_via_unmigrate", None
        except Exception as exc:
            status, error = "unattach_failed", str(exc)
        ledger.record_unattach(lid, status, error)
        results.append({"legacy_id": lid, "hs_note_id": nid, "status": status, "error": error})
    json.dump(results, sys.stdout, indent=2)
    print()
    return 1 if any(r["status"] == "unattach_failed" for r in results) else 0


def _add_emit_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hierarchy-out", required=True, help="bronze CSV (library_hierarchy)")
    p.add_argument("--silver-preview-out", help="optional offline 19-col parity CSV")
    p.add_argument("--card", help=f"entity card path (default {DEFAULT_CARD_PATH})")
    p.add_argument("--path-prefix", default=None, help='legacy ancestry above the root, e.g. "30 Sales"')
    p.add_argument("--root-name", default="", help="legacy name of the walked root")
    p.add_argument("--segment-depth", type=int, default=0)
    p.add_argument("--id-scheme", choices=["pathcode-hash", "pathcode", "hash"], default="pathcode-hash")
    p.add_argument("--owner-prefix", default="mirx")
    p.add_argument("--owner-email")
    p.add_argument("--owner-fullname")
    p.add_argument("--exclude-images", action="store_true")
    p.add_argument("--require-anchor", action="store_true", help="silver preview: drop files with no company anchor")


def cmd_review_export(args: argparse.Namespace) -> int:
    """Operator queues from the hierarchy CSV (no network)."""
    import csv

    rows = read_hierarchy_csv(Path(args.hierarchy))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0].keys()) if rows else []
    queues = {
        "deal_candidates.csv": [r for r in rows if r.get("asset_class") == "deal_candidate"],
        "parked_for_review.csv": [r for r in rows if r.get("asset_class") == "parked_for_review"],
        "orphans.csv": [r for r in rows if r.get("is_dir") in ("False", "false", "0") and not r.get("company_node_key")],
        "multi_parent.csv": [r for r in rows if (r.get("parents_count") or "1") not in ("", "1")],
        "companies.csv": [r for r in rows if r.get("libr_category") == "company_folder"],
    }
    summary: dict = {"out_dir": str(out_dir.resolve())}
    for name, subset in queues.items():
        with (out_dir / name).open("w", encoding="utf-8", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=cols)
            w.writeheader()
            w.writerows(subset)
        summary[name] = len(subset)
    card = load_library_card(Path(args.card)) if getattr(args, "card", None) else load_library_card()
    anchors = qualify_deal_anchors(rows, card)
    deferred = deal_documents(rows, anchors)
    anchor_cols = ["deal_node_key", "legacy_deal_id", "deal_name", "anchor_kind", "company_node_key",
                   "pdf_count", "deal_candidate_count", "asset_count", "link"]
    with (out_dir / "deal_anchors.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=anchor_cols); w.writeheader()
        for a in sorted(anchors.values(), key=lambda a: (a.company_node_key, a.anchor_kind, a.deal_name)):
            w.writerow({k: getattr(a, k) for k in anchor_cols})
    with (out_dir / "deal_documents.csv").open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=cols + ["legacy_deal_id", "deal_name"]); w.writeheader(); w.writerows(deferred)
    summary["deal_anchors.csv"] = len(anchors)
    summary["deal_documents.csv"] = len(deferred)
    summary["deal_decisions.csv"] = write_decisions_template(rows, out_dir / "deal_decisions.csv", card=card)
    json.dump(summary, sys.stdout, indent=2)
    print()
    return 0


def cmd_properties(args: argparse.Namespace) -> int:
    """Schema propagation into HubSpot (definitions only; values flow through StackSync).

    ensure  — create missing groups/properties from the card (gated; never modifies existing ones)
    verify  — definitions vs the built silver model (dbt catalog) → StackSync mapping sheet
    """
    settings = Settings.from_env()
    card = load_library_card(Path(args.card))
    plan = load_property_plan(card.raw)
    client = _client_or_none(settings)
    if args.mode == "ensure":
        _banner("properties ensure", [("property/group create", APPROVE_PROPERTY_CREATE_ENV)])
        if client is None:
            results = [{"object_type": f.object_type, "kind": "property", "name": f.name, "column": f.column,
                        "type": f.type, "status": "unknown_no_token", "error": "HUBSPOT_SANDBOX_TOKEN unset"}
                       for f in plan.fields]
        else:
            results = ensure_properties(client, plan, live=_gate(APPROVE_PROPERTY_CREATE_ENV))
        print(json.dumps(results, indent=2))
        print(f"properties ensure: {summarize(results)}", file=sys.stderr)
        return 1 if any(r["status"] in ("failed", "type_mismatch") for r in results) else 0

    _banner("properties verify", [])
    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path) if catalog_path.exists() else None
    if catalog is None:
        print(f"  no dbt catalog at {catalog_path} — checking HubSpot definitions only "
              f"(run the dbt step; it generates the catalog)", file=sys.stderr)
    rows = verify_properties(client, plan, catalog)
    sheet = write_mapping_sheet(rows, Path(args.out_dir) / "stacksync_mapping.csv")
    print(json.dumps(rows, indent=2))
    print(f"properties verify: {summarize(rows)} → {sheet}", file=sys.stderr)
    return 1 if any(r["status"] not in OK_STATUSES and r["status"] != "unknown_no_token" for r in rows) else 0


def cmd_ledger_export(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _banner("ledger-export", [("bq load of ledger tables", APPROVE_BQ_LOAD_ENV)])
    ledger = _ledger(settings, args.ledger)
    paths = ledger.export_tables(Path(args.out_dir))
    schema_dir = Path(__file__).parent / "sql" / "ledger"
    rc = 0
    for table in LEDGER_TABLES:
        cmd = [
            "bq", "load", "--source_format=CSV", "--skip_leading_rows=1", "--allow_quoted_newlines",
            "--replace", f"{args.dataset}.{table}", str(paths[table]), str(schema_dir / f"{table}.schema.json"),
        ]
        print(" ".join(shlex.quote(c) for c in cmd))
        if _gate(APPROVE_BQ_LOAD_ENV):
            rc = subprocess.run(cmd).returncode or rc
    return rc


def cmd_deals(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    _banner("deals (pass 2)", [("deal create + associations", APPROVE_DEAL_CREATE_ENV)])
    decisions = read_decisions(
        Path(args.decisions),
        default_pipeline=args.pipeline or os.environ.get("MRLOAD_DEAL_PIPELINE"),
        default_stage=args.dealstage or os.environ.get("MRLOAD_DEAL_STAGE"),
    )
    if not decisions:
        print("decisions file is empty", file=sys.stderr)
        return 1
    ledger = _ledger(settings, args.ledger)
    card = load_library_card(Path(args.card)) if args.card else load_library_card()
    assoc = tuple(((card.raw.get("deal_inference") or {}).get("pass_2_associates")) or ["deal_candidate"])
    results = resolve_deals(
        decisions, hierarchy_rows=read_hierarchy_csv(Path(args.hierarchy)), client=_client_or_none(settings),
        ledger=ledger, live_create=_gate(APPROVE_DEAL_CREATE_ENV), associate_classes=assoc,
    )
    json.dump(results, sys.stdout, indent=2)
    print()
    return 1 if any(r["status"] in ("failed", "partial") for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.library_files.runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    walk = sub.add_parser("walk", help="Drive API DFS of a card scope root → hierarchy CSV.")
    walk.add_argument("--root", help="scope root name or drive id from the card (default: first root)")
    walk.add_argument("--credentials", help="service-account JSON (default: GOOGLE_APPLICATION_CREDENTIALS / ADC)")
    walk.add_argument("--max-depth", type=int)
    _add_emit_args(walk)
    walk.set_defaults(func=cmd_walk)

    index = sub.add_parser("index", help="Offline emission from an rclone lsjson manifest.")
    index.add_argument("--manifest", required=True)
    _add_emit_args(index)
    index.set_defaults(func=cmd_index)

    bq = sub.add_parser("bq-load", help="Load the hierarchy CSV into BigQuery (gated dry-run).")
    bq.add_argument("--dataset", required=True)
    bq.add_argument("--table", default="library_hierarchy")
    bq.add_argument("--source-uri", required=True, help="local CSV path or gs:// URI")
    bq.add_argument("--replace", action="store_true")
    bq.set_defaults(func=cmd_bq_load)

    comp = sub.add_parser("companies", help="Resolve company folders to HubSpot companies (gated create).")
    comp.add_argument("--hierarchy", required=True)
    comp.add_argument("--ledger")
    comp.set_defaults(func=cmd_companies)

    att = sub.add_parser("attach", help="Upload assets and attach as notes on their company (two gates).")
    att.add_argument("--hierarchy", required=True)
    att.add_argument("--ledger")
    att.add_argument("--credentials")
    att.add_argument("--cache-dir")
    att.add_argument("--include-classes", default="asset,deal_candidate,parked_for_review",
                     help="asset classes to attach (shortcut is never attached)")
    att.set_defaults(func=cmd_attach)

    un = sub.add_parser("unmigrate", help="Delete attached notes using the ledger (gated).")
    un.add_argument("--ledger")
    un.set_defaults(func=cmd_unmigrate)

    rev = sub.add_parser("review-export", help="Operator queues + deal_decisions.csv template (offline).")
    rev.add_argument("--hierarchy", required=True)
    rev.add_argument("--out-dir", default=".mrload/review")
    rev.add_argument("--card", default=None)
    rev.set_defaults(func=cmd_review_export)

    props = sub.add_parser("properties", help="HubSpot property definitions for StackSync: ensure (gated) / verify + mapping sheet.")
    props.add_argument("mode", choices=["ensure", "verify"])
    props.add_argument("--card", default=str(DEFAULT_CARD_PATH))
    props.add_argument("--catalog", default="dbt/target/catalog.json", help="dbt catalog of the built silver models (verify)")
    props.add_argument("--out-dir", default=".mrload/review", help="where stacksync_mapping.csv is written (verify)")
    props.set_defaults(func=cmd_properties)

    lex = sub.add_parser("ledger-export", help="Step 6: ledger tables → CSV → BigQuery (gated bq load).")
    lex.add_argument("--ledger")
    lex.add_argument("--out-dir", default=".mrload/ledger_export")
    lex.add_argument("--dataset", required=True, help="raw dataset, e.g. mrload_raw")
    lex.set_defaults(func=cmd_ledger_export)

    dl = sub.add_parser("deals", help="Pass 2: deals from the approved decisions file (gated).")
    dl.add_argument("--decisions", required=True, help="edited .mrload/review/deal_decisions.csv")
    dl.add_argument("--hierarchy", required=True, help="library_hierarchy.csv (which PO/Billing notes belong to each anchor)")
    dl.add_argument("--card", default=None)
    dl.add_argument("--ledger")
    dl.add_argument("--pipeline", help="default pipeline id (or MRLOAD_DEAL_PIPELINE)")
    dl.add_argument("--dealstage", help="default dealstage id (or MRLOAD_DEAL_STAGE)")
    dl.set_defaults(func=cmd_deals)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
