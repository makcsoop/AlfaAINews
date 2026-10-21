"""Определение типа и кодировки CSV без базы."""

from pathlib import Path

import pytest

from newsai.db.case_loader import detect

COMPANY_HEADER = "SYNTHETIC_U;MAINOKVED_CODE;MAINOKVED_NAME;REVENUE_COMPANY;CITY"


def _write(tmp_path: Path, name: str, text: str, enc: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return p


def test_kinds_by_header_not_filename(tmp_path):
    # у физлиц и платежей в брифе одно имя файла — тип берём из заголовка
    pay = _write(tmp_path, "df_Fid_Uid_link_hack.csv", "BENEFICIARY;PAYER;PAY_AMT;PAY_CNT\nU_1;U_2;1.0;1\n")
    per = _write(tmp_path, "x.csv", 'SYNTHETIC_F;CITY_NAME;RC_NAME;SYNTHETIC_U\nF_1;"Мск ""Юг""";М;U_1\n')
    com = _write(tmp_path, "y.csv", f"{COMPANY_HEADER}\nU_1;46.51;Опт;1.0;Москва\n")
    unk = _write(tmp_path, "z.csv", "A;B\n1;2\n")
    assert detect(pay)[0] == "case_payments"
    assert detect(per)[0] == "case_persons"
    assert detect(com)[0] == "case_companies"
    assert detect(unk)[0] is None
    assert detect(com)[2] == ";"


def test_utf8_cyrillic_across_64kb_boundary(tmp_path):
    # регрессия: граница чтения посреди русской буквы не должна переключать файл в cp1251
    row = "U_1;46.51;Торговля оптовая компьютерами;1.0;Москва\n"
    for pad in range(2):  # кириллица — 2 байта: сдвиг на 1 байт гарантирует разрез
        raw = (COMPANY_HEADER + "\n" + "U_" + "0" * pad + row[2:] + row * 3000).encode()
        try:
            raw[: 64 * 1024].decode("utf-8")
        except UnicodeDecodeError:
            break
    else:
        pytest.fail("не удалось построить файл с разрезанной буквой")
    p = tmp_path / "big.csv"
    p.write_bytes(raw)
    assert detect(p)[1] == "utf-8-sig"


def test_cp1251_detected(tmp_path):
    p = _write(tmp_path, "cp.csv", f"{COMPANY_HEADER}\nU_1;46.51;Опт;1.0;Москва\n", enc="cp1251")
    assert detect(p)[1] == "cp1251"
