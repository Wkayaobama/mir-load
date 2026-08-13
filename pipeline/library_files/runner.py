"""Standalone CLI entry point for the mr-load library_files module.

Sub-commands:
  index    — Parse an rclone lsjson manifest and emit the silver library
             index CSV (the hierarchical index + FK inference candidates).
  bq-load  — Print (dry-run default) or execute the bq load of a silver CSV
             into the library index table.

Mirrors ic-load's runner conventions: argparse sub-commands, approval gates
via env vars that must be explicitly "1" for live writes, dry-run default.

  MRLOAD_APPROVE_BQ_LOAD=1   enables the live `bq load` execution.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .manifest import load_manifest
from .silver_library import SilverIndexBuilder, silver_columns
from .walker import IMAGE_EXTS, DriveTreeWalker

APPROVE_BQ_LOAD_ENV = "MRLOAD_APPROVE_BQ_LOAD"


def cmd_index(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    walker = DriveTreeWalker(
        path_prefix=args.path_prefix,
        root_name=args.root_name,
        segment_depth=args.segment_depth,
        exclude_exts=IMAGE_EXTS if args.exclude_images else (),
    )
    builder = SilverIndexBuilder(
        walker,
        owner_prefix=args.owner_prefix,
        owner_email=args.owner_email,
        owner_fullname=args.owner_fullname,
        id_scheme=args.id_scheme,
        require_inference=args.require_inference,
    )
    out_path = Path(args.out).resolve()
    stats = builder.write_csv(load_manifest(manifest_path), out_path)
    json.dump(
        {
            "out": str(out_path),
            "columns": silver_columns(args.owner_prefix),
            "stats": stats.__dict__,
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0 if stats.written_rows else 1


def cmd_bq_load(args: argparse.Namespace) -> int:
    live = os.environ.get(APPROVE_BQ_LOAD_ENV, "").strip() == "1"
    print(
        f"library_files runner — bq-load gate: {'LIVE' if live else 'DRY'} "
        f"({APPROVE_BQ_LOAD_ENV}={'1' if live else 'unset'})",
        file=sys.stderr,
    )
    cmd = [
        "bq", "load", "--source_format=CSV", "--skip_leading_rows=1",
        "--allow_quoted_newlines", "--replace" if args.replace else "--noreplace",
        f"{args.dataset}.{args.table}", args.source_uri,
    ]
    schema_path = Path(__file__).parent / "sql" / "library_index.schema.json"
    cmd.append(str(schema_path))
    print(" ".join(shlex.quote(c) for c in cmd))
    if not live:
        return 0
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.library_files.runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    index = sub.add_parser(
        "index", help="Build the silver library index CSV from an rclone lsjson manifest."
    )
    index.add_argument("--manifest", required=True, help="rclone lsjson -R --hash output file")
    index.add_argument("--out", required=True, help="destination CSV path")
    index.add_argument(
        "--path-prefix", default="",
        help='legacy ancestry above the walked root, e.g. "30 Sales"',
    )
    index.add_argument(
        "--root-name", default="",
        help='legacy name of the walked root, e.g. "20 opportunities and customer data"',
    )
    index.add_argument("--segment-depth", type=int, default=1)
    index.add_argument(
        "--id-scheme", choices=["pathcode-hash", "pathcode", "hash"],
        default="pathcode-hash",
    )
    index.add_argument("--owner-prefix", default="mirx")
    index.add_argument("--owner-email")
    index.add_argument("--owner-fullname")
    index.add_argument("--exclude-images", action="store_true")
    index.add_argument(
        "--require-inference", action="store_true",
        help="drop rows with no company/deal candidate (icalps FK-filter analogue)",
    )
    index.set_defaults(func=cmd_index)

    bq = sub.add_parser("bq-load", help="Load a silver CSV into BigQuery (gated dry-run).")
    bq.add_argument("--dataset", required=True)
    bq.add_argument("--table", default="library_index")
    bq.add_argument("--source-uri", required=True, help="gs:// URI or local CSV path")
    bq.add_argument("--replace", action="store_true")
    bq.set_defaults(func=cmd_bq_load)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
