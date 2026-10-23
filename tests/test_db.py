"""Интеграция с Postgres: отдельная база newsai_test на том же сервере. Без Postgres тесты пропускаются."""

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from backend.db import models as m
from backend.db.case_loader import load_case_dir
from backend.db.init import create_schema, load_fixtures
from backend.ingest.store import insert_items, known_native_ids
from backend.schemas import RawItem

TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://newsai:newsai@localhost:55432/newsai_test"
)


@pytest.fixture(scope="module")
def engine():
    url = make_url(TEST_URL)
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as e:  # нет локального Postgres — пропускаем, а не падаем
        pytest.skip(f"Postgres недоступен: {e}")
    eng = create_engine(url)
    with eng.begin() as conn:
        create_schema(conn)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def case_dir(tmp_path):
    (tmp_path / "companies.csv").write_text(
        "SYNTHETIC_U;MAINOKVED_CODE;MAINOKVED_NAME;CL_TYPE;NEGATIVEMARK;"
        "CL_PRODUCT_RKO_MARK;CL_PRODUCT_GAR_MARK;CL_PRODUCT_LOA_MARK;CL_PRODUCT_LEA_MARK;"
        "CL_PRODUCT_DEP_MARK;CL_PRODUCT_ZPP_MARK;FTS_FLAG;FTS_COUNTRY_NAME;FACTORING_MARK;"
        "REVENUE_COMPANY;GROUP_ID;CITY;REGION;OKFS_NAME;COMPANY_AGE_YEARS_GRADATION;ECO_MARK_LABEL;"
        "AMOUNT_WORKERS_FNS_GRADATION;AMOUNT_WORKERS_SPARK_GRADATION\n"
        "U_1;46.51;Опт;CLIENT;0.0;1.0;0.0;1.0;0.0;0.0;1.0;1.0;КИТАЙ, ТУРЦИЯ;0.0;20000000000.0;g1;Москва;Москва;Ч;16-25;;16-100;\n"
        "U_2;41.2;Стройка;PROSPECT;0.0;0.0;0.0;0.0;0.0;1.0;0.0;0.0;;0.0;5000.0;g1;Казань;Татарстан;Ч;1-3;Рост;Менее 15;\n"
        "U_X;01.11;А;CLIENT;0.0;0.0;0.0;0.0;0.0;0.0;0.0;0.0;;0.0;1.0;g2;Омск;Омск;Ч;1-3;;Менее 15;\n"
        "U_X;62.01;Б;CLIENT;0.0;0.0;0.0;0.0;0.0;0.0;0.0;0.0;;0.0;2.0;g3;Омск;Омск;Ч;1-3;;Менее 15;\n",
        encoding="utf-8",
    )
    (tmp_path / "payments.csv").write_text(
        "BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\nU_2;U_1;1000.0;3\nU_1;U_1;5.0;1\nU_9;U_1;7.5;2\n",
        encoding="utf-8",
    )
    (tmp_path / "persons.csv").write_text(
        'SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U\nF_1;"Мск ""Юг""";МОСКОВСКИЙ;U_1\n', encoding="utf-8"
    )
    return tmp_path


def test_case_load_typed_views(engine, case_dir):
    with engine.begin() as conn:
        assert load_case_dir(conn, case_dir) == {"case_companies": 4, "case_payments": 3, "case_persons": 1}
        rows = {r.company_id: r for r in conn.execute(text("SELECT * FROM companies"))}
        assert set(rows) == {"U_1", "U_2"}  # неоднозначный U_X исключён
        u1 = rows["U_1"]
        assert u1.loans is True and u1.deposits is False and u1.fts_flag is True
        assert u1.fts_countries == ["КИТАЙ", "ТУРЦИЯ"]
        assert u1.sector == "46" and rows["U_2"].sector == "41" and float(u1.revenue) == 2e10
        assert rows["U_2"].fts_countries is None and rows["U_2"].eco_stage == "Рост"
        assert conn.execute(text("SELECT count(*) FROM payments WHERE is_self")).scalar() == 1
        city = conn.execute(text("SELECT city_name FROM case_persons")).scalar()
        assert city == 'Мск "Юг"'


def test_case_reload_skips_unchanged(engine, case_dir):
    with engine.begin() as conn:
        load_case_dir(conn, case_dir)
        conn.execute(text("CREATE OR REPLACE VIEW dev2_probe AS SELECT company_id FROM companies"))
    with engine.begin() as conn:
        load_case_dir(conn, case_dir)  # файлы те же — таблицы не пересоздаются
        assert conn.execute(text("SELECT to_regclass('dev2_probe')")).scalar() is not None
    with (case_dir / "payments.csv").open("a", encoding="utf-8") as f:
        f.write("U_1;U_2;1.0;1\n")
    with engine.begin() as conn:
        assert load_case_dir(conn, case_dir)["case_payments"] == 4


def _item(native: str, title: str = "Заголовок") -> RawItem:
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    return RawItem(
        id=f"id-{native}",
        source_id="prime",
        native_id=native,
        title=title,
        published_at=now,
        fetched_at=now,
        content_hash=f"h-{native}",
    )


def test_insert_items_idempotent(engine):
    with engine.begin() as conn:
        assert insert_items(conn, [_item("1"), _item("2")]) == 2
        assert insert_items(conn, [_item("1"), _item("3")]) == 1
        assert known_native_ids(conn, "prime", ["1", "2", "3", "4"]) == {"1", "2", "3"}
        assert insert_items(conn, []) == 0


def test_fixture_reload_updates_columns(engine):
    with engine.begin() as conn:
        load_fixtures(conn)
        conn.execute(text("UPDATE bank_positions SET thesis = 'старое' "))
        load_fixtures(conn)
        theses = conn.execute(select(m.bank_positions.c.thesis)).scalars().all()
        assert theses and "старое" not in theses


def test_graph_end_to_end(engine, case_dir):
    from backend.profile.graph import build_graphs, get_graph, save_graphs

    with engine.begin() as conn:
        load_case_dir(conn, case_dir, force=True)
    with engine.connect() as read, engine.begin() as write:
        assert save_graphs(write, build_graphs(read)) >= 2
    with engine.connect() as conn:
        g = get_graph(conn, "U_1")
        # U_1 платит U_2 (та же группа g1 — внутригрупповой) и U_9 (вне базы); самоплатёж исключён
        assert g.total_out == pytest.approx(7.5) and g.intragroup_out == pytest.approx(1000)
        assert [c.company_id for c in g.suppliers] == ["U_9"]
        assert g.suppliers[0].in_dataset is False and g.sector_shares_suppliers == {"outside": 1.0}
        assert g.group_id == "g1" and "U_2" in g.group_members and g.sparse is True
        assert get_graph(conn, "U_NOPE") is None


def test_snapshot_roundtrip(engine, tmp_path, monkeypatch):
    # А6: снимок знаний Р1 переносится на чужой стенд без OCR и без Kimi
    from backend.db import demo

    monkeypatch.setattr(demo, "SNAPSHOT", tmp_path)
    with engine.begin() as conn:
        load_fixtures(conn)
        before = conn.execute(select(m.bank_positions.c.id, m.bank_positions.c.data)).all()
        sent = demo.export(conn)
    assert sent["bank_positions"] == len(before) > 0
    with engine.begin() as conn:
        conn.execute(m.bank_positions.delete())
        conn.execute(m.market_knowledge.delete())
        got = demo.load(conn)
        after = conn.execute(select(m.bank_positions.c.id, m.bank_positions.c.data)).all()
    assert got["bank_positions"] == len(before) and sorted(after) == sorted(before)


def test_analysis_saved_by_card_ref(engine):
    # Б4: разбор хранится отдельно и находится по analysis_ref карточки
    from backend.db.analyses import get_analysis, save_analysis
    from backend.fixtures import load_bundle

    b = load_bundle("rate_14")
    a = b["analysis"][0]
    card = b["insight_card"][0]
    with engine.begin() as conn:
        ref = save_analysis(conn, a.model_copy(update={"status": "on_review"}))
        ref = save_analysis(conn, a)  # повторная запись — обновление, не дубль
        got = get_analysis(conn, card.analysis_ref)
    assert ref == card.analysis_ref and got == a
