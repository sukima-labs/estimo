"""Votes → verdict: weighted mean, then hard rules that no model can override.

Thresholds and weights live here and are tuned against evaluation/, where the metric that
rules is: no legitimate listing in red.
"""

from __future__ import annotations

from mikiwame.models import Case, HardRule, Level, Signal, Verdict, Vote
from mikiwame.rubric import REMOTE_MARKERS

VOTER_WEIGHT = {
    "text": 1.0, "patterns": 0.8, "recidivism": 0.7, "market": 0.7, "iban": 0.6, "phone": 0.4,
    # Informative only (Telegram test 01-10-2026: on its own it turned a reassuring case amber
    # by lowering the confidence). Its signal reaches the report; the level comes from the rest.
    "address": 0.0,
    # Informative too (2026-10-19): agency fees charged to the tenant are common and legal
    # advice, not a scam marker.
    "fees": 0.0,
}  # fmt: skip
DETERMINISTIC = {"patterns", "iban", "phone", "recidivism", "market"}

RED_SCORE = 0.55
AMBER_SCORE = 0.2
RED_MIN_CONFIDENCE = 0.5
GREEN_MIN_CONFIDENCE = 0.5


def merge_signals(votes: list[Vote]) -> tuple[list[Signal], dict[str, set[str]]]:
    """One signal per code (prefer one with a quote), plus which voters raised each code."""
    by_code: dict[str, Signal] = {}
    sources: dict[str, set[str]] = {}
    for v in votes:
        for s in v.signals:
            sources.setdefault(s.code, set()).add(v.voter)
            cur = by_code.get(s.code)
            if cur is None or (s.quote and not cur.quote):
                by_code[s.code] = s
    ordered = sorted(by_code.values(), key=lambda s: -s.weight)
    return ordered, sources


def hard_rules(sources: dict[str, set[str]]) -> list[HardRule]:
    codes = set(sources)

    def deterministic(*cs: str) -> bool:
        return any(sources.get(c, set()) & DETERMINISTIC for c in cs)

    rules: list[HardRule] = []
    markers = [c for c in REMOTE_MARKERS if c in codes]
    if "pay_before_visit" in codes and markers:
        rules.append(
            HardRule(
                code="remote_payment",
                floor=Level.RED,
                locked=deterministic(*markers),
                message="Pago antes de ver la vivienda combinado con una visita imposible o a "
                "distancia: es el patrón más común de estafa en alquileres.",
            )
        )
    if "lookalike_domain" in codes and codes & {"platform_payment", "pay_before_visit"}:
        rules.append(
            HardRule(
                code="lookalike_payment",
                floor=Level.RED,
                locked=True,
                message="Pago a través de un enlace que imita a un portal o plataforma.",
            )
        )
    if "ai_instructions" in codes:
        rules.append(
            HardRule(
                code="ai_instructions",
                floor=Level.AMBER,
                # Locked only on a deterministic hit in the advertiser's own text; the LLM alone
                # could mistake a user's question for an injection (review 2026-10-01).
                locked=deterministic("ai_instructions"),
                message="El material intenta manipular a sistemas de análisis automático.",
            )
        )
    if "untraceable_payment" in codes:
        rules.append(
            HardRule(
                code="untraceable_payment",
                floor=Level.AMBER,
                locked=deterministic("untraceable_payment"),
                message="Método de pago imposible de rastrear.",
            )
        )
    for code in ("keys_by_post", "platform_payment"):
        if code in codes:
            rules.append(
                HardRule(
                    code=code,
                    floor=Level.AMBER,
                    locked=deterministic(code),
                    message="Entrega de llaves o pago a través de terceros.",
                )
            )
    return rules


def insufficient(case: Case) -> bool:
    """Too little material to call anything green: no chat and almost no pasted text."""
    advertiser_msgs = sum(1 for m in case.chat if m.sender != "user")
    return advertiser_msgs == 0 and len(case.pasted_text.strip()) < 200


def aggregate(votes: list[Vote], case: Case) -> Verdict:
    active = [v for v in votes if v.confidence > 0]
    weights = [VOTER_WEIGHT.get(v.voter, 0.5) * v.confidence for v in active]
    total = sum(weights)
    score = sum(w * v.score for w, v in zip(weights, active, strict=True)) / total if total else 0.0
    if active:
        base = sum(VOTER_WEIGHT.get(v.voter, 0.5) for v in active)
        confidence = total / base
        if any(v.score > 0.3 for v in active) and any(v.score < -0.3 for v in active):
            confidence -= 0.15  # voters disagree
    else:
        confidence = 0.0
    lacking = insufficient(case)
    if lacking:
        confidence = min(confidence, 0.45)
    confidence = max(0.0, min(1.0, confidence))

    signals, sources = merge_signals(votes)
    rules = hard_rules(sources)

    if score >= RED_SCORE and confidence >= RED_MIN_CONFIDENCE:
        level = Level.RED
    elif score >= AMBER_SCORE:
        level = Level.AMBER
    else:
        level = Level.GREEN
    for r in rules:
        level = max(level, r.floor)
    if level == Level.GREEN and (lacking or confidence < GREEN_MIN_CONFIDENCE):
        level = Level.AMBER

    return Verdict(
        level=level,
        confidence=round(confidence, 3),
        score=round(score, 3),
        signals=signals,
        hard_rules=rules,
        insufficient_info=lacking,
        injection_detected=bool(sources.get("ai_instructions", set()) & DETERMINISTIC),
    )
