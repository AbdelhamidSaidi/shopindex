"""Run state and output. SQLite keeps runs resumable; CSV/TSV is the deliverable."""

from __future__ import annotations

import csv
import dataclasses
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .models import CSV_COLUMNS, Vendor

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    slug        TEXT PRIMARY KEY,
    page_id     TEXT,
    url         TEXT,
    status      TEXT,
    score       REAL,
    discovered  TEXT,
    scraped_at  TEXT,
    payload     TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    slug        TEXT PRIMARY KEY,
    source      TEXT,
    hint        TEXT,
    added       TEXT,
    done        INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dedupe (
    key         TEXT PRIMARY KEY,
    slug        TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_done ON queue(done);
CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(status);
"""


class Store:
    def __init__(self, path: Path | str = "data/state.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- queue -------------------------------------------------------------

    def enqueue(self, slug: str, source: str = "", hint: str = "") -> bool:
        """Add a candidate. Returns False if we already know about it."""
        if not slug:
            return False
        with closing(self.db.cursor()) as cur:
            cur.execute("SELECT 1 FROM queue WHERE slug=?", (slug,))
            if cur.fetchone():
                return False
            cur.execute(
                "INSERT INTO queue (slug, source, hint, added) VALUES (?,?,?,?)",
                (slug, source, hint, datetime.now().isoformat(timespec="seconds")),
            )
        self.db.commit()
        return True

    def pending(self, limit: int | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM queue WHERE done=0 ORDER BY rowid"
        if limit:
            q += f" LIMIT {int(limit)}"
        return list(self.db.execute(q))

    def mark_done(self, slug: str) -> None:
        self.db.execute("UPDATE queue SET done=1 WHERE slug=?", (slug,))
        self.db.commit()

    def queue_counts(self) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT SUM(done=0) AS pending, SUM(done=1) AS done FROM queue"
        ).fetchone()
        return (row["pending"] or 0, row["done"] or 0)

    # -- results -----------------------------------------------------------

    def already_scraped(self, slug: str) -> bool:
        cur = self.db.execute("SELECT 1 FROM pages WHERE slug=? AND status='ok'", (slug,))
        return cur.fetchone() is not None

    def is_duplicate(self, vendor: Vendor) -> str | None:
        """Return the slug we already hold this vendor under, if any."""
        for key in vendor.dedupe_keys():
            row = self.db.execute("SELECT slug FROM dedupe WHERE key=?", (key,)).fetchone()
            if row and row["slug"] != vendor.slug:
                return row["slug"]
        return None

    def save(self, vendor: Vendor) -> None:
        payload = json.dumps(dataclasses.asdict(vendor), ensure_ascii=False, default=str)
        self.db.execute(
            "INSERT INTO pages (slug, page_id, url, status, score, discovered, scraped_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(slug) DO UPDATE SET page_id=excluded.page_id, url=excluded.url,"
            " status=excluded.status, score=excluded.score, scraped_at=excluded.scraped_at,"
            " payload=excluded.payload",
            (
                vendor.slug,
                vendor.page_id,
                vendor.facebook_url,
                vendor.fetch_status,
                vendor.score,
                datetime.now().isoformat(timespec="seconds"),
                vendor.scraped_at,
                payload,
            ),
        )
        for key in vendor.dedupe_keys():
            self.db.execute(
                "INSERT OR IGNORE INTO dedupe (key, slug) VALUES (?,?)", (key, vendor.slug)
            )
        self.db.commit()

    def vendors(self, min_score: float = 0.0, only_usable: bool = True) -> list[Vendor]:
        out: list[Vendor] = []
        fields = {f.name for f in dataclasses.fields(Vendor)}
        for row in self.db.execute("SELECT payload FROM pages WHERE status='ok' ORDER BY score DESC"):
            data = json.loads(row["payload"])
            v = Vendor(**{k: val for k, val in data.items() if k in fields})
            if v.score < min_score:
                continue
            if only_usable and not v.is_usable():
                continue
            out.append(v)
        return out

    def stats(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) n FROM pages GROUP BY status")
        return {r["status"] or "unknown": r["n"] for r in rows}


def write_table(vendors: list[Vendor], path: Path | str, delimiter: str = ",") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, delimiter=delimiter,
                           quoting=csv.QUOTE_MINIMAL, extrasaction="ignore")
        w.writeheader()
        for v in vendors:
            w.writerow(v.to_row())
    return path
