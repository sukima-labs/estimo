"""Common voter interface: vote(case) -> Vote."""

from __future__ import annotations

import math
from typing import Protocol

from mikiwame.models import Case, Signal, Vote
from mikiwame.rubric import point


class Voter(Protocol):
    name: str

    async def vote(self, case: Case) -> Vote: ...


def signal(
    code: str, quote: str | None = None, message: str | None = None, message_en: str | None = None
) -> Signal:
    p = point(code)
    return Signal(
        code=code,
        weight=p.weight,
        message=message or p.message,
        quote=quote,
        advice=p.advice,
        message_en=message_en,
    )


def score_from(signals: list[Signal]) -> float:
    """Squash the summed weights into -1..1. Weights come from the rubric, never from a model."""
    return max(-1.0, min(1.0, math.tanh(sum(s.weight for s in signals))))
