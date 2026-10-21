import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).resolve().parent / "data"

# Тесты не зависят от папки, из которой запущен pytest
os.chdir(ROOT)


@pytest.fixture
def root() -> Path:
    return ROOT
