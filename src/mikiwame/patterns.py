"""Modus operandi: anonymous features of each analysed case, and their summary.

Patricio, 2026-10-01: «alimentar nuestra base de datos con timos encontrados para entender
comportamientos y formas de operación». Features carry no identifier and no free text, so they
can outlive the 30-day retention; the redacted conversation itself is kept only when the user
opts in (research_cases).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter

from mikiwame.extract.text import iban_info, phone_region
from mikiwame.models import Case, Verdict

_AMOUNT = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,5})(?:,\d{1,2})?\s?(?:€|euros?)", re.I)
_MONEY = re.compile(
    r"\b(?:reserva\w*|señal|senal|fianza|transferencia|bizum|pag\w+|ingres\w+|western|"
    r"giro|dep[oó]sito)\b|€|euros?",
    re.I,
)


def amounts(text: str) -> list[int]:
    return [int(re.sub(r"[.\s]", "", m.group(1))) for m in _AMOUNT.finditer(text)]


def features(case: Case, verdict: Verdict, *, listing_known: bool, listing_removed: bool) -> dict:
    adv = [m.text for m in case.chat if m.sender != "user"]
    first_money = next((i for i, t in enumerate(adv, start=1) if _MONEY.search(t)), None)
    mentioned = sorted({a for t in adv for a in amounts(t)})
    return {
        "day": dt.date.today().isoformat(),
        "level": verdict.level.key,
        "signal_codes": ",".join(sorted(s.code for s in verdict.signals)),
        "hard_rules": ",".join(sorted(r.code for r in verdict.hard_rules)),
        "channel": case.channel,
        "property_type": case.property_type,
        "iban_countries": ",".join(sorted({iban_info(i).country for i in case.advertiser_ibans})),
        "phone_countries": ",".join(
            sorted({phone_region(p) or "?" for p in case.advertiser_phones})
        ),
        "amounts_eur": json.dumps(mentioned),
        "first_money_msg": first_money,
        "advertiser_msgs": len(adv),
        "listing_known": listing_known,
        "listing_removed": listing_removed,
    }


def summarize(rows: list[dict]) -> str:
    """Plain-text digest for Patricio (CLI). Rows: dicts of case_features."""
    if not rows:
        return "Todavía no hay casos analizados."
    lines = [f"{len(rows)} casos analizados"]
    by_level = Counter(r["level"] for r in rows)
    lines.append("niveles: " + ", ".join(f"{k} {v}" for k, v in by_level.most_common()))
    risky = [r for r in rows if r["level"] in ("red", "amber")]
    if risky:
        combos = Counter(r["signal_codes"] for r in risky if r["signal_codes"])
        if combos:
            lines.append("combinaciones más frecuentes en rojo/ámbar:")
            lines += [f"  {n:3}× {c}" for c, n in combos.most_common(8)]
        single = Counter(c for r in risky for c in (r["signal_codes"] or "").split(",") if c)
        lines.append(
            "señales: " + (", ".join(f"{c} {n}" for c, n in single.most_common(12)) or "ninguna")
        )
        lines.append(
            "canal: "
            + ", ".join(f"{k} {v}" for k, v in Counter(r["channel"] for r in risky).most_common())
        )
        for col, label in (("iban_countries", "IBAN"), ("phone_countries", "teléfono")):
            c = Counter(x for r in risky for x in (r[col] or "").split(",") if x)
            if c:
                lines.append(
                    f"país del {label}: " + ", ".join(f"{k} {v}" for k, v in c.most_common())
                )
        money = [r["first_money_msg"] for r in risky if r["first_money_msg"]]
        if money:
            money.sort()
            lines.append(
                f"pide dinero en el mensaje nº {money[len(money) // 2]} (mediana, n={len(money)})"
            )
        amts = sorted(a for r in risky for a in json.loads(r["amounts_eur"] or "[]"))
        if amts:
            lines.append(
                f"importes mencionados: mediana {amts[len(amts) // 2]} €, "
                f"rango {amts[0]}–{amts[-1]} € (n={len(amts)})"
            )
    return "\n".join(lines)
