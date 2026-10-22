"""DbDataSource на SQLite in-memory: таблицы Р1 с `data` как TEXT (JSONB/pgvector в SQLite не поддерживаются)."""

import json
import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from backend.agents.steps import get_tool_source, run_for_client, set_tool_source
from backend.agents.tools.db_source import DbDataSource
from backend.agents.tools.source import FixtureDataSource
from backend.fixtures import load_bundle

BORROWER, DEPOSITOR = "U_DEMO_BORROWER", "U_DEMO_DEPOSITOR"

# таблица -> (колонка ключа, ключ в bundle)
TABLES = {
    "raw_items": ("id", "raw_item"),
    "stories": ("id", "story"),
    "event_facts": ("story_id", "event_facts"),
    "bank_positions": ("id", "bank_position"),
    "market_knowledge": ("id", "market_knowledge"),
    "company_profiles": ("company_id", "company_profile"),
    "counterparty_graphs": ("company_id", "counterparty_graph"),
}


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    b = load_bundle("rate_14")
    with eng.begin() as c:
        for table, (key, bkey) in TABLES.items():
            extra = ", published_at TEXT, dropped_reason TEXT" if table == "raw_items" else ""
            c.execute(text(f"CREATE TABLE {table} ({key} TEXT PRIMARY KEY{extra}, data TEXT)"))
            for obj in b[bkey]:
                d = obj.model_dump(mode="json")
                params = {"k": d[key], "d": json.dumps(d)}
                if table == "raw_items":
                    params.update(pa=d["published_at"], dr=d.get("dropped_reason"))
                    sql = "INSERT INTO raw_items (id, published_at, dropped_reason, data) VALUES (:k, :pa, :dr, :d)"
                else:
                    sql = f"INSERT INTO {table} ({key}, data) VALUES (:k, :d)"
                c.execute(text(sql), params)
    return eng


def test_objects_match_fixture(engine):
    db, fx = DbDataSource(engine), FixtureDataSource("rate_14")
    assert db.stories() == fx.stories() and db.event_facts() == fx.event_facts()
    assert db.bank_positions() == fx.bank_positions()
    assert db.market_knowledge() == fx.market_knowledge()
    assert {i.id for i in db.raw_items()} == {i.id for i in fx.raw_items() if not i.dropped_reason}
    assert db.profile(BORROWER) == fx.profile(BORROWER)
    assert db.counterparties(BORROWER) == fx.counterparties(BORROWER)


def test_company_isolation(engine):
    db = DbDataSource(engine)
    assert db.profile(BORROWER).company_id == BORROWER
    assert db.profile(DEPOSITOR).company_id == DEPOSITOR
    assert db.profile("U_OTHER") is None and db.counterparties("U_OTHER") is None
    assert db.profile("x' OR '1'='1") is None


def test_broken_row_skipped(engine, caplog):
    with engine.begin() as c:
        c.execute(text("INSERT INTO bank_positions (id, data) VALUES ('bad1', '{\"id\": 1}')"))
        c.execute(text("INSERT INTO bank_positions (id, data) VALUES ('bad2', 'не json')"))
    with caplog.at_level(logging.WARNING):
        got = DbDataSource(engine).bank_positions()
    assert got == FixtureDataSource("rate_14").bank_positions()
    assert "пропущена строка" in caplog.text


def test_raw_items_limit(engine):
    assert len(DbDataSource(engine, raw_items_limit=1).raw_items()) == 1


def test_pipeline_on_db_equals_fixture(engine):
    sid = load_bundle("rate_14")["story"][0].id
    try:
        set_tool_source(FixtureDataSource("rate_14"))
        a, _ = run_for_client(sid, BORROWER, "cfo", sector="46")
        set_tool_source(DbDataSource(engine))
        b, tr = run_for_client(sid, BORROWER, "cfo", sector="46")
    finally:
        set_tool_source(None)
    assert tr.steps[-1] == "handler:PUBLISHED:ok"
    skip = {"generated_at", "trace_id"}
    assert a.model_dump(mode="json", exclude=skip) == b.model_dump(mode="json", exclude=skip)


def test_factory_db_unavailable_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("TOOL_SOURCE", "db")
    monkeypatch.setattr("backend.db.engine.get_engine", lambda: create_engine("sqlite:////nonexistent_dir/x.db"))
    set_tool_source(None)
    try:
        with caplog.at_level(logging.WARNING):
            assert isinstance(get_tool_source(), FixtureDataSource)
    finally:
        set_tool_source(None)
    assert "TOOL_SOURCE=db" in caplog.text
