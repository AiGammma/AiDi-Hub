"""Operator CLI: ``python -m cinebot.cli ...``

    init-db
    export aggregate --requested-by "Acme Research" [--out taste.jsonl]
    export vectors   --requested-by "Acme Research" [--include-ratings]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .app import build_container
from .config import Settings
from .db.session import init_db


async def _run(args: argparse.Namespace) -> None:
    settings = Settings()
    # The CLI never talks to TMDB; a dummy key avoids requiring one for DB-only commands.
    settings.tmdb_api_key = settings.tmdb_api_key or "unused"
    c = build_container(settings)
    try:
        await init_db(c.engine)
        if args.cmd == "init-db":
            print("ok")
            return
        if args.dataset == "aggregate":
            rows = await c.exporter.aggregate(args.requested_by)
        else:
            rows = await c.exporter.vectors(args.requested_by, include_ratings=args.include_ratings)
        out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
        try:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            if args.out:
                out.close()
        print(f"{len(rows)} rows", file=sys.stderr)
    finally:
        await c.aclose()


def main() -> None:
    p = argparse.ArgumentParser(prog="cinebot.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    e = sub.add_parser("export")
    e.add_argument("dataset", choices=["aggregate", "vectors"])
    e.add_argument("--requested-by", required=True, help="who the dataset is for (audit log)")
    e.add_argument("--include-ratings", action="store_true", help="vectors only; per-title ratings")
    e.add_argument("--out")
    asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    main()
