"""One-off import of a pre-Postgres install: python -m memory_book.migrate_sqlite [data-dir]

Copies books and photo metadata from data-dir/memory_book.db into PostgreSQL and uploads the photo files
from data-dir/assets/ to the object store. Safe to re-run: existing rows are kept, files are re-uploaded.
"""

import json
import sqlite3
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from .storage import CONTENT_TYPES, default_store


def main(data_dir: Path) -> None:
    src = sqlite3.connect(data_dir / "memory_book.db")
    src.row_factory = sqlite3.Row
    store = default_store()
    with store.pool.connection() as conn:
        for r in src.execute("SELECT * FROM assets"):
            conn.execute("""INSERT INTO assets (id, filename, width, height, taken_at, description, ext, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
                         (r["id"], r["filename"], r["width"], r["height"], r["taken_at"], r["description"] or "",
                          r["ext"], r["created_at"]))
            for f in (data_dir / "assets" / r["id"]).glob("*"):
                store.blobs.put(f"assets/{r['id']}/{f.name}", f.read_bytes(), CONTENT_TYPES[f.suffix[1:]])
        books = src.execute("SELECT * FROM books").fetchall()
        for r in books:
            conn.execute("""INSERT INTO books (id, title, doc, version, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
                         (r["id"], r["title"], Jsonb(json.loads(r["json"])), r["version"], r["created_at"], r["updated_at"]))
    print(f"Imported {len(books)} books.")
    store.close()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
