"""Measure the adversarial step on its own, with preliminary verdicts we know are right or wrong.

- Legit fixtures get a forced RED built from plausible misreadings (quotes from their own text):
  a good adversary softens them.
- Scam fixtures get their real (correct) unlocked RED: a good adversary leaves them red.

    uv run python -m evaluation.adversary --model opencode-go:gpt-6-luna --think 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

from evaluation.fixtures import load_all, oracle_case
from evaluation.run import OUT_DIR
from mikiwame.adversarial import Adversary, should_review
from mikiwame.aggregate import aggregate
from mikiwame.config import load_settings
from mikiwame.llm.client import LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.models import Level, Vote
from mikiwame.store.sql import Store
from mikiwame.voters.base import score_from, signal
from mikiwame.voters.iban import IbanVoter
from mikiwame.voters.patterns import PatternVoter

# Misreadings a text model could plausibly make on legit fixtures: (code, literal quote).
MISREAD = {
    "l01_agencia": [
        ("pay_before_visit", "El día de la firma se paga el primer mes y la fianza"),
        ("urgency", "Claro, tenemos visitas el sábado a las 11:00"),
    ],
    "l03_propietario_fuera_real": [
        ("owner_away", "Vivo en Múnich desde hace años"),
        ("pay_before_visit", "me haces la transferencia de la fianza y el primer mes"),
    ],
    "l04_agencia_prisa": [
        ("pay_before_visit", "se paga una señal de 300 € que se descuenta de la fianza"),
        ("urgency", "habría que decidirlo ese mismo día"),
    ],
    "l05_habitacion_bizum_iban_erroneo": [
        ("pay_before_visit", "me haces un Bizum con la fianza (350 €)"),
        ("untraceable_payment", "me haces un Bizum con la fianza"),
    ],
    "l08_propietaria_mayor": [
        ("owner_away", "Yo ahora estoy fuera, en Guadalajara"),
        ("pay_before_visit", "me dejas una señal ese día"),
    ],
    "l09_agencia_reserva_web": [
        ("pay_before_visit", "la reserva son 500 € por transferencia"),
        ("leave_platform", "te enviamos por correo el contrato de reserva"),
    ],
}


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="evaluation.adversary")
    ap.add_argument("--model", required=True)
    ap.add_argument("--think", choices=["0", "1"], default="0")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--effort", help="reasoning effort with --think 1 (low, medium…)")
    args = ap.parse_args(argv)
    settings = load_settings(
        database_url=f"sqlite:///{OUT_DIR / 'eval.db'}",
        model_adversarial=args.model,
        model_adversarial_fallback=None,
        budget_daily_usd_adversarial=1e9,
    )
    store = Store(settings.database_url)
    router = LLMRouter(settings, store, LLMClient(env=settings.provider_env()))
    adversary = Adversary(router, think=args.think == "1", effort=args.effort)

    jobs = []
    for fx in load_all():
        case = oracle_case(fx)
        det = [IbanVoter().vote_sync(case), PatternVoter().vote_sync(case)]
        if fx.is_scam and fx.expect[0] == "red":
            # A correct red that only LLM signals hold (so the adversary is allowed to act).
            sigs = [signal("pay_before_visit"), signal("owner_away")]
            text = Vote(voter="text", score=score_from(sigs), confidence=0.85, signals=sigs)
            prelim = aggregate([text], case)
            if det[1].confidence:  # keep only scams whose red is not locked by patterns
                continue
            jobs.append((fx, case, prelim, "keep_red"))
        elif not fx.is_scam and fx.id in MISREAD:
            sigs = [signal(code, quote=q) for code, q in MISREAD[fx.id]]
            text = Vote(voter="text", score=score_from(sigs), confidence=0.85, signals=sigs)
            prelim = aggregate([text, det[0]], case)
            prelim.level = Level.RED
            jobs.append((fx, case, prelim, "soften"))

    sem = asyncio.Semaphore(4)
    rows = []

    async def one(fx, case, prelim, want, run):
        async with sem:
            case = case.model_copy(deep=True)
            case.case_id = f"adv-{fx.id}-{run}-{int(time.time() * 1000) % 10**9}"
            assert should_review(prelim)
            t0 = time.monotonic()
            out = await adversary.review(case, prelim)
            dt = time.monotonic() - t0
            cost = sum(r["cost_usd"] for r in store.case_usage(case.case_id))
            good = (out.level == Level.AMBER) if want == "soften" else (out.level == Level.RED)
            rows.append(
                (fx.id, want, out.level.key, good, round(dt, 1), cost, out.adversarial_notes)
            )

    await asyncio.gather(*(one(*j, run) for run in range(args.repeat) for j in jobs))
    rows.sort()
    for r in rows:
        mark = "ok" if r[3] else "!!"
        notes = json.dumps(r[6], ensure_ascii=False)[:160]
        print(f"{mark} {r[0]:36} want={r[1]:9} got={r[2]:5} {r[4]:5.1f}s ${r[5]:.4f} {notes}")
    soften = [r for r in rows if r[1] == "soften"]
    keep = [r for r in rows if r[1] == "keep_red"]
    summary = {
        "model": args.model,
        "think": args.think == "1",
        "effort": args.effort,
        "softened_wrong_reds": f"{sum(r[3] for r in soften)}/{len(soften)}",
        "kept_right_reds": f"{sum(r[3] for r in keep)}/{len(keep)}",
        "latency_s_mean": round(statistics.mean(r[4] for r in rows), 1),
        "latency_s_max": max(r[4] for r in rows),
        "cost_usd_mean": round(statistics.mean(r[5] for r in rows), 5),
    }
    print(json.dumps(summary, ensure_ascii=False))
    await router.client.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
