import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from stokowski.linear import LinearClient


ISO_NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def cache_db(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE issue (
            id TEXT PRIMARY KEY, identifier TEXT, title TEXT,
            state_id TEXT, state_name TEXT, project_id TEXT,
            labels_json TEXT, assignee_id TEXT,
            updated_at TEXT, cached_at TEXT
        );
        CREATE TABLE comment (
            id TEXT PRIMARY KEY, issue_id TEXT, body TEXT, author_id TEXT,
            created_at TEXT, updated_at TEXT, cached_at TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.execute("INSERT INTO meta VALUES ('last_reconcile_at', ?)", (ISO_NOW,))
    conn.execute(
        "INSERT INTO issue VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("u1", "SYN-100", "test", "s-todo", "Todo", "p1",
         '["bug"]', None, ISO_NOW, ISO_NOW),
    )
    conn.commit()
    conn.close()
    return db


class TestCacheFirst:
    @pytest.mark.asyncio
    async def test_reads_from_cache_when_fresh(self, cache_db):
        # Patch the underlying _graphql so we can prove it was NOT called.
        client = LinearClient("https://api.linear.app/graphql", "k1", cache_db_path=cache_db)
        client._graphql = AsyncMock(side_effect=AssertionError("should not hit Linear"))
        issues = await client.fetch_candidate_issues("p1", ["Todo"])
        assert len(issues) == 1
        assert issues[0].identifier == "SYN-100"

    @pytest.mark.asyncio
    async def test_falls_through_to_linear_when_cache_unset(self, tmp_path):
        client = LinearClient("https://api.linear.app/graphql", "k1", cache_db_path=None)
        client._graphql = AsyncMock(return_value={
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        })
        issues = await client.fetch_candidate_issues("p1", ["Todo"])
        client._graphql.assert_called_once()


class TestFetchByStates:
    @pytest.mark.asyncio
    async def test_reads_from_cache(self, cache_db):
        # Add second issue in a different state name
        conn = sqlite3.connect(cache_db)
        conn.execute(
            "INSERT INTO issue VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("u2", "SYN-101", "second", "s-ip", "In Progress", "p1",
             '[]', None, ISO_NOW, ISO_NOW),
        )
        conn.commit()
        conn.close()

        client = LinearClient("https://api.linear.app/graphql", "k1", cache_db_path=cache_db)
        client._graphql = AsyncMock(side_effect=AssertionError("should not hit Linear"))
        issues = await client.fetch_issues_by_states("p1", ["Todo", "In Progress"])
        assert {i.identifier for i in issues} == {"SYN-100", "SYN-101"}


class TestFetchComments:
    @pytest.mark.asyncio
    async def test_reads_from_cache(self, cache_db):
        conn = sqlite3.connect(cache_db)
        for cid, body in (("c1", "first"), ("c2", "second")):
            conn.execute(
                "INSERT INTO comment VALUES (?,?,?,?,?,?,?)",
                (cid, "u1", body, "a1", ISO_NOW, ISO_NOW, ISO_NOW),
            )
        conn.commit()
        conn.close()

        client = LinearClient("https://api.linear.app/graphql", "k1", cache_db_path=cache_db)
        client._graphql = AsyncMock(side_effect=AssertionError("should not hit Linear"))
        comments = await client.fetch_comments("u1")
        assert {c["body"] for c in comments} == {"first", "second"}
