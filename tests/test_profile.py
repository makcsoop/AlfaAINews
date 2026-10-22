"""Профиль v0 из CSV и привязка архетипов к компаниям."""

from pathlib import Path

from newsai.profile.archetypes import load_archetypes, resolve
from newsai.profile.build import build_profiles, profile_from_row, sector_of
from newsai.profile.load import load_profiles

# Строка в формате настоящего CSV: разделитель «;», флаги 0.0/1.0, десятичная запятая
REAL_HEADER = (
    "SYNTHETIC_U;ORG_TYPE;OKFS_NAME;COMPANY_AGE_YEARS_GRADATION;ECO_MARK_LABEL;MAINOKVED_CODE;"
    "MAINOKVED_NAME;NEGATIVEMARK;AMOUNT_WORKERS_FNS_GRADATION;AMOUNT_WORKERS_SPARK_GRADATION;CL_TYPE;"
    "CL_PRODUCT_RKO_MARK;CL_PRODUCT_GAR_MARK;CL_PRODUCT_LOA_MARK;CL_PRODUCT_LEA_MARK;CL_PRODUCT_DEP_MARK;"
    "CL_PRODUCT_ZPP_MARK;FTS_FLAG;FTS_COUNTRY_NAME;FACTORING_MARK;REVENUE_COMPANY;GROUP_ID;CITY;REGION"
)
REAL_ROWS = [
    "U_1;КОММ_ОРГ;Частная;10-20 лет;Рост;46.69;Опт машинами;0.0;251-1000;;действующий;"
    "1.0;0.0;1.0;1.0;0.0;1.0;1.0;КИТАЙ, ТУРЦИЯ;0.0;42000000000,50;G_1;Москва;г. Москва",
    "U_2;КОММ_ОРГ;Частная;5-10 лет;;46.9;Опт неспец.;0.0;;;потенциальный;"
    "1.0;;0.0;0.0;1.0;0.0;0.0;;0.0;1500000000;G_2;Казань;Республика Татарстан",
    # повторяющийся технический id (как U_428697) — исключается
    "U_9;КОММ_ОРГ;Частная;;;41.20;Стройка;0.0;;;потенциальный;0.0;0.0;0.0;0.0;0.0;0.0;0.0;;0.0;1;G_3;;",
    "U_9;КОММ_ОРГ;Частная;;;62.01;ИТ;0.0;;;потенциальный;0.0;0.0;0.0;0.0;0.0;0.0;0.0;;0.0;1;G_4;;",
]


def _real_csv(tmp_path: Path) -> Path:
    p = tmp_path / "df_Uid_data_hack.csv"
    p.write_text("\n".join([REAL_HEADER, *REAL_ROWS]) + "\n", encoding="utf-8")
    return tmp_path


def test_sector_ignores_okved_spelling():
    assert sector_of("41.2") == sector_of("41.20") == "41"
    assert sector_of(None) is None
    assert sector_of("4") is None


def test_profile_from_real_csv_format(tmp_path):
    profiles = load_profiles(_real_csv(tmp_path))
    assert set(profiles) == {"U_1", "U_2"}  # U_9 неоднозначен
    p = profiles["U_1"]
    assert p.okved_code == "46.69" and p.sector == "46" and p.region == "г. Москва"
    assert p.revenue == 42_000_000_000.5
    assert p.products == {
        "rko": True,
        "guarantees": False,
        "loans": True,
        "leasing": True,
        "deposits": False,
        "payroll": True,
        "factoring": False,
    }
    assert p.fts_flag is True and p.fts_country == "КИТАЙ, ТУРЦИЯ"
    assert p.group_id == "G_1" and p.negative_mark is False
    # два крупнейших в секторе 46: U_1 первый
    assert (p.revenue_rank_in_okved, p.revenue_peers_in_okved) == (1, 2)
    assert profiles["U_2"].revenue_rank_in_okved == 2


def test_missing_values_lower_completeness(tmp_path):
    profiles = load_profiles(_real_csv(tmp_path))
    full, sparse = profiles["U_1"], profiles["U_2"]
    assert full.completeness == 1.0
    assert sparse.completeness is not None and sparse.completeness < full.completeness
    assert "guarantees" not in sparse.products  # пустой флаг — неизвестно, а не «нет»
    assert sparse.eco_stage is None


def test_empty_id_skipped():
    row = dict.fromkeys(REAL_HEADER.split(";"), "")
    assert profile_from_row(row).company_id == ""
    assert build_profiles([row]) == {}


def test_synthetic_profiles_load():
    profiles = load_profiles(Path("data/case_synthetic"))
    assert len(profiles) == 200
    assert all(p.sector and p.completeness for p in profiles.values())


def test_archetypes_bound_to_distinct_companies():
    profiles = load_profiles(Path("data/case_synthetic"))
    cfg = load_archetypes()
    assert len(cfg.archetypes) == 5
    picked = resolve(profiles, cfg)
    assert all(picked.values()), picked
    ids = [p.company_id for p in picked.values() if p]
    assert len(set(ids)) == len(ids)

    opt = picked["wholesale_borrower"]
    assert opt and opt.sector == "46" and opt.products["loans"] and opt.fts_flag
    exp = picked["exporter_depositor"]
    assert exp and exp.products["deposits"] and exp.fts_flag
    assert picked["construction"].sector in {"41", "42", "43"}  # type: ignore[union-attr]
    # в синтетике нет 09.1 — срабатывает запасное правило «добыча»
    assert picked["oilfield_services"].sector == "06"  # type: ignore[union-attr]


def test_pinned_company_wins():
    profiles = load_profiles(Path("data/case_synthetic"))
    cfg = load_archetypes()
    some = next(p for p in profiles.values() if p.sector == "41")
    cfg.archetypes[3].company_id = some.company_id
    picked = resolve(profiles, cfg)
    assert picked["construction"] is some
    assert some.company_id not in {p.company_id for k, p in picked.items() if p and k != "construction"}
