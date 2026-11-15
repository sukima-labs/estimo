"""Run the fixtures through the real pipeline and measure it.

    uv run python -m evaluation.run --mode oracle --repeat 2
    uv run python -m evaluation.run --mode vision --text-model opencode-go:gpt-5.6-luna

Spends real quota (shared with the house). Results go to var/eval/<timestamp>.jsonl and a
summary is printed. The metric that rules: legit fixtures in red must be 0.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field

from evaluation.fixtures import Fixture, load_all, oracle_case
from mikiwame import PROJECT_DIR
from mikiwame.config import load_settings
from mikiwame.extract.build import Draft, DraftItem, build_case
from mikiwame.llm.client import LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.pipeline import AnalysisFailed, Analyzer
from mikiwame.store.market import MarketStore
from mikiwame.store.sql import Store

OUT_DIR = PROJECT_DIR / "var" / "eval"


@dataclass
class Result:
    fixture: str
    label: str
    expect: list[str]
    run: int
    level: str | None
    acceptable: bool
    ideal: bool
    score: float | None
    confidence: float | None
    signals: list[str]
    hard_rules: list[str]
    softened: bool
    cost_usd: float
    latency_s: float
    calls: list[dict]
    error: str | None = None
    # Expected signals that did not show up, and forbidden ones that did.
    missing_signals: list[str] = field(default_factory=list)
    unwanted_signals: list[str] = field(default_factory=list)


def _draft_for_vision(fx: Fixture, case_id: str) -> Draft:
    """What a user would send: the ad pasted as text plus the chat screenshots."""
    text = "\n".join(filter(None, [fx.listing_url, fx.listing_text, fx.pasted]))
    items = [DraftItem("text", text=text)] if text else []
    items += [DraftItem("image", image=p.read_bytes()) for p in fx.image_paths()]
    return Draft(user_id=0, case_id=case_id, items=items)


async def run_one(analyzer: Analyzer, fx: Fixture, run: int, mode: str) -> Result:
    case_id = f"eval-{fx.id}-{run}-{int(time.time() * 1000) % 10**9}"
    t0 = time.monotonic()
    error = None
    verdict = None
    try:
        if mode == "vision":
            draft = _draft_for_vision(fx, case_id)
            case = await build_case(
                draft,
                vision=analyzer.vision,
                max_images=analyzer.settings.max_images,
                max_text_chars=analyzer.settings.max_text_chars,
            )
        else:
            case = oracle_case(fx)
            case.case_id = case_id
        out = await analyzer.analyze_case(case, tg_user_id=0, persist=False)
        verdict = out.verdict
    except (AnalysisFailed, Exception) as e:  # noqa: BLE001 - the report must go on
        error = f"{type(e).__name__}: {e}"
    calls = analyzer.store.case_usage(case_id)
    level = verdict.level.key if verdict else None
    got = {s.code for s in verdict.signals} if verdict else set()
    return Result(
        fixture=fx.id,
        label=fx.label,
        expect=list(fx.expect),
        run=run,
        level=level,
        acceptable=level in fx.expect,
        ideal=level == fx.expect[0],
        score=verdict.score if verdict else None,
        confidence=verdict.confidence if verdict else None,
        signals=[s.code for s in verdict.signals] if verdict else [],
        hard_rules=[r.code for r in verdict.hard_rules] if verdict else [],
        softened=bool(verdict and verdict.softened_by_adversary),
        cost_usd=sum(c["cost_usd"] for c in calls),
        latency_s=round(time.monotonic() - t0, 1),
        calls=[
            {k: c[k] for k in ("role", "model", "input_tokens", "output_tokens", "cost_usd", "ok")}
            for c in calls
        ],
        error=error,
        missing_signals=[c for c in fx.expect_signals if verdict and c not in got],
        unwanted_signals=[c for c in fx.absent_signals if c in got],
    )


def summarize(results: list[Result], usd_eur: float) -> dict:
    done = [r for r in results if r.level]
    scams = [r for r in done if r.label == "scam"]
    legit = [r for r in done if r.label == "legit"]
    red = [r for r in done if r.level == "red"]
    tp = sum(1 for r in red if r.label == "scam")
    costs = [r.cost_usd for r in done]
    fixtures = {f.id: f for f in load_all()}
    expected = sum(len(fixtures[r.fixture].expect_signals) for r in done if r.fixture in fixtures)
    missed = sum(len(r.missing_signals) for r in done)
    return {
        "runs": len(results),
        "errors": len(results) - len(done),
        "legit_in_red": sum(1 for r in legit if r.level == "red"),
        "red_precision": round(tp / len(red), 3) if red else None,
        "red_recall": round(tp / len(scams), 3) if scams else None,
        "acceptable": round(sum(r.acceptable for r in done) / len(done), 3) if done else None,
        "ideal": round(sum(r.ideal for r in done) / len(done), 3) if done else None,
        "scam_green": sum(1 for r in scams if r.level == "green"),
        "signal_recall": round(1 - missed / expected, 3) if expected else None,
        "unwanted_signals": sum(len(r.unwanted_signals) for r in done),
        "cost_usd_mean": round(statistics.mean(costs), 5) if costs else None,
        "cost_usd_max": round(max(costs), 5) if costs else None,
        "cost_eur_mean": round(statistics.mean(costs) * usd_eur, 5) if costs else None,
        "cost_usd_total": round(sum(r.cost_usd for r in results), 4),
        "latency_s_mean": round(statistics.mean(r.latency_s for r in done), 1) if done else None,
        "latency_s_max": max((r.latency_s for r in done), default=None),
    }


def print_table(results: list[Result]) -> None:
    by_fx: dict[str, list[Result]] = {}
    for r in results:
        by_fx.setdefault(r.fixture, []).append(r)
    for fid, rs in by_fx.items():
        levels = " ".join((r.level or "ERR")[:3] for r in rs)
        ok = "ok " if all(r.acceptable for r in rs) else "!! "
        first = rs[0]
        cost = statistics.mean(r.cost_usd for r in rs)
        latency = statistics.mean(r.latency_s for r in rs)
        err = f" ERROR {first.error[:120]}" if first.error else ""
        missing = sorted({c for r in rs for c in r.missing_signals})
        unwanted = sorted({c for r in rs for c in r.unwanted_signals})
        if missing:
            err += f" MISSING {','.join(missing)}"
        if unwanted:
            err += f" UNWANTED {','.join(unwanted)}"
        print(
            f"{ok}{fid:36} {first.label:5} expect={'/'.join(first.expect):11} got={levels:15} "
            f"${cost:.4f} {latency:5.1f}s {','.join(first.signals)}{err}"
        )


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="evaluation.run")
    ap.add_argument("--mode", choices=["oracle", "vision"], default="oracle")
    ap.add_argument("--pattern", default="*")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--vision-model")
    ap.add_argument("--text-model")
    ap.add_argument("--adversarial-model")
    ap.add_argument("--think-text", choices=["0", "1"])
    ap.add_argument("--think-adversarial", choices=["0", "1"])
    ap.add_argument("--effort-adversarial", help='reasoning effort; "" = the model default')
    ap.add_argument("--tag", default="")
    args = ap.parse_args(argv)

    overrides: dict[str, object] = {"database_url": f"sqlite:///{OUT_DIR / 'eval.db'}"}
    if args.vision_model:
        overrides["model_vision"] = args.vision_model
    if args.text_model:
        overrides["model_text"] = args.text_model
    if args.adversarial_model:
        overrides["model_adversarial"] = args.adversarial_model
    if args.think_text:
        overrides["think_text"] = args.think_text == "1"
    if args.think_adversarial:
        overrides["think_adversarial"] = args.think_adversarial == "1"
    if args.effort_adversarial is not None:
        overrides["effort_adversarial"] = args.effort_adversarial or None
    # The eval measures models, not our daily budget switch.
    overrides |= {f"budget_daily_usd_{r}": 1e9 for r in ("vision", "text", "adversarial")}
    settings = load_settings(**overrides)
    store = Store(settings.database_url)
    router = LLMRouter(
        settings,
        store,
        LLMClient(timeout_s=settings.llm_timeout_s, env=settings.provider_env()),
    )
    analyzer = Analyzer(settings, store, router, MarketStore(settings.market_database_url))

    fixtures = load_all(args.pattern)
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(fx: Fixture, run: int) -> Result:
        async with sem:
            return await run_one(analyzer, fx, run, args.mode)

    t0 = time.monotonic()
    results = await asyncio.gather(
        *(guarded(fx, run) for run in range(args.repeat) for fx in fixtures)
    )
    results.sort(key=lambda r: (r.fixture, r.run))
    print_table(results)
    summary = summarize(results, settings.usd_eur)
    config = {
        "mode": args.mode,
        "vision": settings.model_vision,
        "text": settings.model_text,
        "adversarial": settings.model_adversarial,
        "think_text": settings.think_text,
        "think_adversarial": settings.think_adversarial,
        "effort_adversarial": settings.effort_adversarial,
        "repeat": args.repeat,
        "tag": args.tag,
        "wall_s": round(time.monotonic() - t0, 1),
    }
    print(json.dumps({"config": config, "summary": summary}, ensure_ascii=False, indent=1))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT_DIR / f"{stamp}-{args.mode}{'-' + args.tag if args.tag else ''}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"config": config, "summary": summary}, ensure_ascii=False) + "\n")
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"written {out.relative_to(PROJECT_DIR)}")
    await router.client.aclose()
    return 0 if summary["legit_in_red"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
