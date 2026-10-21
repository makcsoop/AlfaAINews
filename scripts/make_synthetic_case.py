"""Синтетические CSV той же структуры, что в кейсе (бриф, приложение 1).

Нужны для тестов и для сдачи («синтетические тестовые данные»), пока и вместо настоящих CSV.
`python scripts/make_synthetic_case.py [out_dir] [n_companies]`
"""

import csv
import random
import sys
from pathlib import Path

OKVED = [
    ("46.69", "Торговля оптовая прочими машинами и оборудованием"),
    ("41.20", "Строительство жилых и нежилых зданий"),
    ("24.10", "Производство чугуна, стали и ферросплавов"),
    ("49.41", "Деятельность автомобильного грузового транспорта"),
    ("10.11", "Переработка и консервирование мяса"),
    ("01.11", "Выращивание зерновых культур"),
    ("47.11", "Торговля розничная в неспециализированных магазинах"),
    ("06.10", "Добыча нефти"),
    ("62.01", "Разработка компьютерного программного обеспечения"),
    ("35.11", "Производство электроэнергии"),
]
CITIES = [
    ("Москва", "г. Москва"),
    ("Екатеринбург", "Свердловская область"),
    ("Казань", "Республика Татарстан"),
    ("Новосибирск", "Новосибирская область"),
    ("Краснодар", "Краснодарский край"),
]
COUNTRIES = ["Китай", "Турция", "Казахстан", "Индия", "ОАЭ"]
PRODUCTS = ["RKO", "GAR", "LOA", "LEA", "DEP", "ZPP"]

COMPANY_COLS = [
    "SYNTHETIC_U",
    "ORG_TYPE",
    "OKFS_NAME",
    "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL",
    "MAINOKVED_CODE",
    "MAINOKVED_NAME",
    "NEGATIVEMARK",
    "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION",
    "CL_TYPE",
    *[f"CL_PRODUCT_{p}_MARK" for p in PRODUCTS],
    "FTS_FLAG",
    "FTS_COUNTRY_NAME",
    "FACTORING_MARK",
    "REVENUE_COMPANY",
    "GROUP_ID",
    "CITY",
    "REGION",
]


def main(out: Path, n: int, seed: int = 14) -> None:
    rnd = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    ids = [f"U{100000 + i}" for i in range(n)]

    with (out / "df_Uid_data_hack.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COMPANY_COLS)
        for i, uid in enumerate(ids):
            code, name = rnd.choice(OKVED)
            city, region = rnd.choice(CITIES)
            fts = rnd.random() < 0.35
            w.writerow(
                [
                    uid,
                    "ЮЛ",
                    rnd.choice(["Частная собственность", "Смешанная российская собственность"]),
                    rnd.choice(["5-10 лет", "10-20 лет", "более 20 лет"]),
                    rnd.choice(["Зрелость", "Рост"]),
                    code,
                    name,
                    int(rnd.random() < 0.05),
                    rnd.choice(["251-1000", "1001-5000", ">5000"]),
                    rnd.choice(["251-1000", "1001-5000", ">5000"]),
                    rnd.choice(["действующий", "потенциальный"]),
                    *[int(rnd.random() < p) for p in (0.95, 0.3, 0.5, 0.3, 0.4, 0.6)],
                    int(fts),
                    rnd.choice(COUNTRIES) if fts else "",
                    int(rnd.random() < 0.15),
                    f"{rnd.uniform(15, 400) * 1e9:.2f}".replace(".", ","),
                    f"G{i // 4}" if rnd.random() < 0.4 else "",
                    city,
                    region,
                ]
            )

    with (out / "df_Fid_Uid_link_hack.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"])
        for j in range(n * 2):
            city, region = rnd.choice(CITIES)
            for uid in rnd.sample(ids, rnd.choice([1, 1, 2])):
                w.writerow([f"F{200000 + j}", city, region, uid])

    with (out / "df_payments_hack.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"])
        for payer in ids:
            for ben in rnd.sample([x for x in ids if x != payer], rnd.randint(3, 12)):
                w.writerow([payer, ben, f"{rnd.lognormvariate(18, 1.5):.2f}", rnd.randint(1, 500)])
    print(f"{n} companies -> {out}")


if __name__ == "__main__":
    main(
        Path(sys.argv[1] if len(sys.argv) > 1 else "data/case_synthetic"),
        int(sys.argv[2]) if len(sys.argv) > 2 else 200,
    )
