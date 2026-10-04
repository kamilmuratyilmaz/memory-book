"""Shared fixtures: a throwaway Postgres schema + RustFS bucket per test, and sample photos."""

import io
import os
import uuid

import psycopg
import pytest
from botocore.exceptions import EndpointConnectionError
from PIL import Image

from memory_book.storage import Store, default_blobs


def jpeg(w, h, color=(180, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://memory_book:memory_book@localhost:5432/memory_book")


@pytest.fixture
def make_store():
    """A Store on a throwaway Postgres schema and RustFS bucket, both removed after the test."""
    made = []

    def make() -> Store:
        name = f"test-{uuid.uuid4().hex[:12]}"
        schema = name.replace("-", "_")
        try:
            with psycopg.connect(DB_URL, autocommit=True) as conn:
                conn.execute(f"CREATE SCHEMA {schema}")
            blobs = default_blobs(bucket=name)
        except (psycopg.OperationalError, EndpointConnectionError) as e:
            pytest.fail(f"Postgres/RustFS not reachable (run: docker compose up -d db rustfs): {e}")
        s = Store(DB_URL, blobs, options=f"-c search_path={schema}")
        made.append((s, schema))
        return s
    yield make
    for s, schema in made:
        s.close()
        s.blobs.delete_prefix("")
        s.blobs.s3.delete_bucket(Bucket=s.blobs.bucket)
        with psycopg.connect(DB_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def store(make_store):
    return make_store()


@pytest.fixture
def photos(store):
    sizes = [(1600, 1067), (1067, 1600), (1200, 1200), (2400, 800), (600, 1800), (1600, 1200)]
    return [store.add_asset(f"p{i}.jpg", jpeg(w, h, (40 * i % 255, 120, 200))) for i, (w, h) in enumerate(sizes)]


