"""Персональная карточка: промпт, сборка из черновика модели, предложение банка, шаблон на случай сбоя."""

from backend.cards.build import CardAssemblyError, assemble_card, compute_confidence
from backend.cards.card import CardResult, CardUnavailable, write_card, write_verified_card
from backend.cards.llm_schema import CardDraft
from backend.cards.offers import BankOffer, find_offer
from backend.cards.template import template_draft
from backend.cards.verify import CardCheck, CardFailure, verify_card

__all__ = [
    "BankOffer",
    "CardAssemblyError",
    "CardCheck",
    "CardDraft",
    "CardFailure",
    "CardResult",
    "CardUnavailable",
    "assemble_card",
    "compute_confidence",
    "find_offer",
    "template_draft",
    "verify_card",
    "write_card",
    "write_verified_card",
]
