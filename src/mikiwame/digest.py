"""Weekly digest for Patricio: reports, real cost per report, spend and modus operandi.

Everything comes from aggregate tables (cases' level, llm_calls, case_features, the market sync
log): no identifier and no free text of any case leaves the database.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from mikiwame.config import Settings
from mikiwame.patterns import summarize
from mikiwame.store import schema
from mikiwame.store.market import MarketStore, sync_log
from mikiwame.store.sql import PUBLIC, Store
from mikiwame.voters.market import zone_es

MADRID = ZoneInfo("Europe/Madrid")
LEVEL_ES = {"green": "verde", "amber": "ámbar", "red": "rojo", None: "fallidos"}


def usage_by_level(store: Store, since: dt.datetime) -> list[tuple]:
    """(level, reports, mean cost USD, max cost USD) per level; level None = failed."""
    calls, cases = schema.llm_calls, schema.cases
    per_case = (
        select(calls.c.case_id, func.sum(calls.c.cost_usd).label("cost"))
        .group_by(calls.c.case_id)
        .subquery()
    )
    q = (
        select(cases.c.level, func.count(), func.avg(per_case.c.cost), func.max(per_case.c.cost))
        .select_from(cases.outerjoin(per_case, per_case.c.case_id == cases.c.id))
        .where(cases.c.created_at >= since)
        .group_by(cases.c.level)
    )
    with store.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(q)]


def spend_by_model(store: Store, since: dt.datetime) -> list[tuple]:
    """(role, model, calls, cost USD, failed calls)."""
    c = schema.llm_calls
    q = (
        select(
            c.c.role,
            c.c.model,
            func.count(),
            func.sum(c.c.cost_usd),
            func.sum(case((c.c.ok, 0), else_=1)),
        )
        .where(c.c.created_at >= since)
        .group_by(c.c.role, c.c.model)
    )
    with store.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(q)]


def feature_rows(store: Store, since_day: dt.date) -> list[dict]:
    """Features carry a day, not a time: whole days after since_day, so each weekly digest
    covers (last Monday, this Monday] and none counts the same day twice."""
    with store.engine.connect() as conn:
        return [
            dict(r._mapping)
            for r in conn.execute(
                select(schema.case_features).where(
                    schema.case_features.c.day > since_day.isoformat()
                )
            )
        ]


def distinct_users(store: Store, since: dt.datetime) -> int:
    with store.engine.connect() as conn:
        q = select(func.count(func.distinct(schema.cases.c.tg_user_id))).where(
            schema.cases.c.created_at >= since
        )
        return int(conn.execute(q).scalar_one())


STALE_DAYS = 14  # idealista sweeps each zone about once a week, irregularly


def market_line(market: MarketStore, *, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.UTC)
    with market.engine.connect() as conn:
        last = conn.execute(select(sync_log).order_by(sync_log.c.id.desc()).limit(1)).first()
    if last is None:
        return "copia del mercado: sin sincronizaciones"
    state = "bien" if last.rc == 0 else f"FALLÓ (rc={last.rc})"
    line = (
        f"copia del mercado: última sincronización {_local(last.started_at):%d-%m %H:%M}, {state}"
    )
    cov = [c for c in market.coverage() if c.category == "viviendas"]
    swept = [c for c in cov if c.last_swept]
    if not swept:
        return line
    newest = max(c.last_swept for c in swept)
    cutoff = now.date() - dt.timedelta(days=STALE_DAYS)
    stale = sorted(
        zone_es(c.municipality) for c in cov if not c.last_swept or c.last_swept < cutoff
    )
    listed = f"{sum(c.listings - c.removed for c in cov):,}".replace(",", ".")
    line += (
        f"\n{len(cov)} zonas de viviendas, {listed} anuncios sin baja; último barrido de "
        f"idealista el {newest:%d-%m}"
    )
    if stale:
        shown = ", ".join(stale[:5]) + (f" y {len(stale) - 5} más" if len(stale) > 5 else "")
        line += f"\n⚠️ {len(stale)} sin barrer en {STALE_DAYS} días: {shown}"
    return line


def build(settings: Settings, *, days: int = 7, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=days)
    store = Store(settings.database_url)
    rate = settings.usd_eur
    lines = [f"📊 Resumen de {days} días ({_local(since):%d-%m} → {_local(now):%d-%m})"]

    levels = usage_by_level(store, since)
    total = sum(n for _, n, _, _ in levels)
    rows = feature_rows(store, since.date())
    # /borrar deletes the cases but keeps their anonymous features.
    deleted = len(rows) - total
    if not total and deleted > 0:
        lines.append(
            f"Análisis: {deleted}, ya borrados por sus usuarios (/borrar); quedan sus fichas "
            "anónimas en «Modus operandi»."
        )
    elif not total:
        lines.append("Ningún análisis en este periodo.")
    else:
        done = [(lvl, n, avg, mx) for lvl, n, avg, mx in levels if lvl]
        by = ", ".join(f"{LEVEL_ES[lvl]} {n}" for lvl, n, _, _ in sorted(levels, key=_order))
        lines.append(f"Análisis: {total} ({by}) de {distinct_users(store, since)} usuario(s)")
        if done:
            n_done = sum(n for _, n, _, _ in done)
            mean = sum((avg or 0) * n for _, n, avg, _ in done) / n_done
            worst = max(mx or 0 for _, _, _, mx in done)
            flag = " ⚠️ por encima del objetivo" if worst * rate >= 0.05 else ""
            lines.append(
                f"Coste por informe: media {_es(mean * rate)} €, máximo {_es(worst * rate)} € "
                f"(objetivo < 0,05 €){flag}"
            )
    spend = spend_by_model(store, since)
    if spend:
        lines.append("Gasto por modelo:")
        for role, model, n, cost, failed in sorted(spend):
            bad = f", {failed} fallida(s)" if failed else ""
            lines.append(f"  {role} · {model}: {n} llamada(s), {_es(cost or 0)} ${bad}")

    outcomes = store.outcome_counts()
    if outcomes:
        n = sum(outcomes.values())
        by = {o: sum(v for (_, oo), v in outcomes.items() if oo == o) for o in ("scam", "legit")}
        legit_red = outcomes.get(("red", "legit"), 0)
        scam_green = outcomes.get(("green", "scam"), 0)
        flag = " ⚠️" if legit_red else ""
        lines.append(
            f"Seguimiento (desde el inicio): {n} respuesta(s), {by['scam']} timo(s), "
            f"{by['legit']} legítimo(s). Legítimos en rojo: {legit_red}{flag}. "
            f"Timos en verde: {scam_green}."
        )
    everyone = store.members_list()
    public = [m for m in everyone if m["campaign"] == PUBLIC]
    members = [m for m in everyone if m["campaign"] != PUBLIC]
    used = sum(m["analyses_used"] or 0 for m in members)
    lines.append(
        f"Beta: {store.beta_seats_taken(idle_days=settings.member_idle_days)} de "
        f"{settings.max_members} plazas ocupadas (activos + invitaciones pendientes), "
        f"{len(members)} miembro(s) en total, {used} análisis de miembros"
    )
    if public or settings.open_access:
        new = sum(1 for m in public if m["joined_at"] >= since)
        lines.append(
            f"Acceso abierto: {len(public)} persona(s) sin invitación ({new} esta semana), "
            f"{sum(m['analyses_used'] or 0 for m in public)} análisis"
        )
    camps = store.campaigns_list()
    if camps:
        lines.append(
            "Invitaciones en grupos: "
            + "; ".join(
                f"{c['name']} {c['uses']}/{c['max_uses']} entradas, {c['analyses']} análisis"
                for c in camps
            )
        )

    if rows:
        lines += ["", "Modus operandi:", summarize(rows)]
    try:
        market = MarketStore(settings.market_database_url)
        lines += ["", market_line(market, now=now)]
    except Exception as e:  # noqa: BLE001 - the digest must go out even without the mirror
        lines += ["", f"copia del mercado: no se pudo leer ({type(e).__name__})"]
    return "\n".join(lines)


def _order(row: tuple) -> int:
    return ["red", "amber", "green", None].index(row[0]) if row[0] in LEVEL_ES else 9


def _local(t: dt.datetime) -> dt.datetime:
    """Stored times are UTC (naive or aware); Patricio reads Madrid time."""
    return (t if t.tzinfo else t.replace(tzinfo=dt.UTC)).astimezone(MADRID)


def _es(x: float) -> str:
    return f"{x:.4f}".replace(".", ",")
