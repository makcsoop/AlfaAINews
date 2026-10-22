"""Чтение компаний кейса из CSV: настоящий файл (data/case) или синтетический (data/case_synthetic)."""

import csv
from collections.abc import Iterator
from pathlib import Path

from newsai.config import get_settings
from newsai.db.case_loader import detect
from newsai.profile.build import build_profiles
from newsai.schemas import CompanyProfile

SYNTHETIC_DIR = Path("data/case_synthetic")


def companies_csv(directory: Path | None = None) -> Path:
    """Файл компаний в папке кейса, иначе в синтетике. Тип файла — по заголовку, как в загрузчике."""
    dirs = [directory] if directory else [Path(get_settings().case_data_dir), SYNTHETIC_DIR]
    for d in dirs:
        for path in sorted(d.glob("*.csv")):
            if detect(path)[0] == "case_companies":
                return path
    raise FileNotFoundError(f"нет CSV компаний в {', '.join(map(str, dirs))}")


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    _, enc, delim, _ = detect(path)
    with path.open(encoding=enc, newline="") as f:
        yield from csv.DictReader(f, delimiter=delim)


def load_profiles(directory: Path | None = None) -> dict[str, CompanyProfile]:
    return build_profiles(read_rows(companies_csv(directory)))
