"""Персональная карточка: промпт, сборка из черновика модели, предложение банка, шаблон на случай сбоя."""

from newsai.cards.build import CardAssemblyError, assemble_card, compute_confidence
from newsai.cards.card import CardResult, CardUnavailable, write_card, write_verified_card
from newsai.cards.llm_schema import CardDraft
from newsai.cards.offers import BankOffer, find_offer
from newsai.cards.template import template_draft
from newsai.cards.verify import CardCheck, CardFailure, verify_card

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
