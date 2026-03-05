from __future__ import annotations

import sqlite3

import pytest

from justdoit_textual.db import Database


@pytest.fixture
def db() -> Database:
    conn = sqlite3.connect(":memory:")
    database = Database(conn)
    database._init_schema()
    try:
        yield database
    finally:
        database.close()
