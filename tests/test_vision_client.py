"""OCR сканов ЦМИ идёт через общий клиент: белый список, бюджет, журнал (инвариант 1)."""

import pytest
from pydantic import SecretStr

from backend.bank.vision import VisionOCR
from backend.llm import LLMClient
from backend.llm.budget import BudgetManager, InMemoryBudgetStore
from backend.llm.client import CompletionRequest, CompletionResponse
from backend.llm.journal import Journal
from backend.llm.settings import LLMSettings

QWEN_VL = "qwen/qwen3-vl-30b-a3b-instruct"


class Stub:
    def __init__(self):
        self.requests: list[CompletionRequest] = []

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        return CompletionResponse(text="  Ставка 14%  ", tokens_in=900, tokens_out=100, provider_cost_rub=0.15)


def make(tmp_path, budget_rub=100.0):
    s = LLMSettings(
        _env_file=None, llm_mode="live", llm_api_key=SecretStr("k"), llm_api_base="http://provider.invalid/v1",
        llm_journal_dir=str(tmp_path / "j"), llm_cache_dir=str(tmp_path / "c"), llm_daily_budget_rub=budget_rub,
    )
    t = Stub()
    c = LLMClient(settings=s, budget=BudgetManager(settings=s, store=InMemoryBudgetStore()),
                  journal=Journal(s.llm_journal_dir), transport=t)
    return c, t


def test_ocr_goes_through_client_budget_and_journal(tmp_path):
    c, t = make(tmp_path)
    ocr = VisionOCR(client=c)
    assert ocr.transcribe_image(model=QWEN_VL, image=b"\xff\xd8jpg", prompt="Распознай") == "Ставка 14%"
    req = t.requests[0]
    assert req.step == "vision" and req.model.provider_name == QWEN_VL
    parts = req.messages[0]["content"]
    assert parts[0] == {"type": "text", "text": "Распознай"} and parts[1]["type"] == "image_url"
    assert c.budget.summary()["by_step"]["vision"]["rub"] == pytest.approx(0.15)
    [entry] = c.journal.entries()
    assert entry.step == "vision" and entry.model == QWEN_VL and entry.cost_rub == pytest.approx(0.15)
    assert ocr.usage.cost == pytest.approx(0.15) and ocr.usage.calls == 1


def test_ocr_rejects_other_model_and_respects_budget(tmp_path):
    c, t = make(tmp_path)
    with pytest.raises(ValueError):
        VisionOCR(client=c).transcribe_image(model="openai/gpt-4o", image=b"x", prompt="p")
    c.budget.record("vision", "r1", 1, 1, 100.0)
    assert VisionOCR(client=c).transcribe_image(model=QWEN_VL, image=b"x", prompt="p") is None
    assert t.requests == []
