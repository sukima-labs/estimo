"""Data access layer. The rest of the code talks to `Store`, never to SQL directly."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import case, create_engine, delete, event, func, insert, or_, select, update
from sqlalchemy.engine import Engine

from mikiwame.llm.client import CallRecord
from mikiwame.models import Case, Verdict, Vote
from mikiwame.store import schema as s


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def add_missing_columns(engine: Engine, metadata) -> None:
    """create_all makes new tables but never alters existing ones: add the columns a table
    gained since it was created (nullable or with a server default only)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                default = f" DEFAULT {col.server_default.arg}" if col.server_default else ""
                conn.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}{default}')
                )


# members.campaign of people who came in through open access, without any invite.
PUBLIC = "public"
# Website visitors have no Telegram id: each one is a negative id derived from an HMAC of their
# IP (web/app.py). Never a member row; all of them together are one branch of the trust tree.
WEB_BRANCH = "web"


def is_web_user(user_id: int) -> bool:
    return user_id < 0


class Store:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        s.metadata.create_all(self.engine)
        add_missing_columns(self.engine, s.metadata)

    # --- LLM usage -------------------------------------------------------------------------

    def record_llm_call(self, case_id: str | None, role: str, rec: CallRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(s.llm_calls).values(
                    created_at=utcnow(),
                    case_id=case_id,
                    role=role,
                    provider=rec.ref.provider.name,
                    model=rec.ref.model.id,
                    protocol=rec.ref.model.protocol,
                    input_tokens=rec.usage.input_tokens,
                    output_tokens=rec.usage.output_tokens,
                    cached_tokens=rec.usage.cached_tokens,
                    cost_usd=rec.cost_usd,
                    latency_ms=rec.latency_ms,
                    ok=rec.ok,
                    error=rec.error,
                )
            )

    def spent_usd_since(self, since: dt.datetime, model_label: str | None = None) -> float:
        q = select(func.coalesce(func.sum(s.llm_calls.c.cost_usd), 0.0)).where(
            s.llm_calls.c.created_at >= since
        )
        if model_label:
            provider, _, model = model_label.partition(":")
            q = q.where(s.llm_calls.c.provider == provider, s.llm_calls.c.model == model)
        with self.engine.connect() as conn:
            return float(conn.execute(q).scalar_one())

    def case_usage(self, case_id: str) -> list[dict]:
        q = select(s.llm_calls).where(s.llm_calls.c.case_id == case_id).order_by(s.llm_calls.c.id)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(q)]

    # --- Cases -----------------------------------------------------------------------------

    def save_case(
        self,
        *,
        case: Case,
        tg_user_id: int,
        retention_days: int,
        verdict: Verdict | None = None,
        votes: Iterable[Vote] = (),
        report_text: str | None = None,
        indicators: Iterable[tuple[str, str]] = (),
        photo_hashes: Iterable[str] = (),
        error: str | None = None,
    ) -> None:
        now = utcnow()
        status = "analyzed" if verdict is not None else "failed"
        row = dict(
            id=case.case_id,
            tg_user_id=tg_user_id,
            created_at=now,
            status=status,
            level=verdict.level.key if verdict else None,
            confidence=verdict.confidence if verdict else None,
            score=verdict.score if verdict else None,
            signal_codes=",".join(sig.code for sig in verdict.signals) if verdict else None,
            hard_rules=",".join(r.code for r in verdict.hard_rules) if verdict else None,
            image_count=case.image_count,
            error=error,
            expires_at=now + dt.timedelta(days=retention_days),
            case_json=case.model_dump_json(),
            verdict_json=verdict.model_dump_json() if verdict else None,
            report_text=report_text,
        )
        with self.engine.begin() as conn:
            conn.execute(insert(s.cases).values(**row))
            for v in votes:
                conn.execute(
                    insert(s.votes).values(
                        case_id=case.case_id,
                        voter=v.voter,
                        score=v.score,
                        confidence=v.confidence,
                        hard_flag=v.hard_flag,
                        signal_codes=",".join(sig.code for sig in v.signals),
                    )
                )
            for h in dict.fromkeys(photo_hashes):
                conn.execute(insert(s.photos).values(case_id=case.case_id, phash=h, created_at=now))
            for kind, digest in set(indicators):
                ind_id = self._upsert_indicator(conn, kind, digest, now)
                conn.execute(
                    insert(s.case_indicators).values(case_id=case.case_id, indicator_id=ind_id)
                )

    @staticmethod
    def _upsert_indicator(conn, kind: str, digest: str, now: dt.datetime) -> int:
        t = s.indicators
        found = conn.execute(select(t.c.id).where(t.c.kind == kind, t.c.hmac == digest)).first()
        if found:
            conn.execute(update(t).where(t.c.id == found.id).values(last_seen=now))
            return found.id
        res = conn.execute(insert(t).values(kind=kind, hmac=digest, first_seen=now, last_seen=now))
        return res.inserted_primary_key[0]

    def get_case(self, case_id: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(s.cases).where(s.cases.c.id == case_id)).first()
            return dict(r._mapping) if r else None

    def count_cases_since(
        self, since: dt.datetime, tg_user_id: int | None = None, status: str | None = "analyzed"
    ) -> int:
        """Cases since a time. Failed attempts do not count against a user's limit (their cost
        is still bounded by the daily budget)."""
        q = select(func.count()).select_from(s.cases).where(s.cases.c.created_at >= since)
        if status is not None:
            q = q.where(s.cases.c.status == status)
        if tg_user_id is not None:
            q = q.where(s.cases.c.tg_user_id == tg_user_id)
        with self.engine.connect() as conn:
            return int(conn.execute(q).scalar_one())

    def indicator_case_count(self, kind: str, digest: str, exclude_case: str | None = None) -> int:
        q = (
            select(func.count())
            .select_from(s.case_indicators.join(s.indicators))
            .where(s.indicators.c.kind == kind, s.indicators.c.hmac == digest)
        )
        if exclude_case:
            q = q.where(s.case_indicators.c.case_id != exclude_case)
        with self.engine.connect() as conn:
            return int(conn.execute(q).scalar_one())

    # --- Registry lookups (recidivism) -----------------------------------------------------

    def related_cases(
        self, indicators: Iterable[tuple[str, str]], *, exclude_user: int
    ) -> dict[str, dict]:
        """Other users' analysed cases sharing any (kind, hmac) with ours:
        {case_id: {"user": id, "level": "red"|…, "kinds": {kind, …}}}."""
        # Disputed identifiers never link cases again (a landlord asked us;
        # info@patriciollorens.com).
        pairs = set(indicators) - self.disputed()
        if not pairs:
            return {}
        revoked = self.revoked_users()
        q = (
            select(s.cases.c.id, s.cases.c.tg_user_id, s.cases.c.level, s.indicators.c.kind,
                   s.indicators.c.hmac)
            .select_from(s.case_indicators.join(s.indicators).join(s.cases))
            .where(
                s.indicators.c.hmac.in_([h for _, h in pairs]),
                s.cases.c.tg_user_id != exclude_user,
                s.cases.c.status == "analyzed",
            )
        )  # fmt: skip
        out: dict[str, dict] = {}
        with self.engine.connect() as conn:
            for r in conn.execute(q):
                if (r.kind, r.hmac) not in pairs or r.tg_user_id in revoked:
                    continue
                info = out.setdefault(
                    r.id, {"user": r.tg_user_id, "level": r.level, "kinds": set()}
                )
                info["kinds"].add(r.kind)
        return out

    def similar_photos(
        self, hashes: Iterable[str], *, exclude_user: int, max_distance: int
    ) -> dict[str, dict]:
        """Other users' cases with a photo within `max_distance` bits of one of ours."""
        from mikiwame.photos import distance

        mine = list(dict.fromkeys(hashes))
        if not mine:
            return {}
        q = (
            select(s.photos.c.case_id, s.photos.c.phash, s.cases.c.tg_user_id, s.cases.c.level)
            .select_from(s.photos.join(s.cases))
            .where(s.cases.c.tg_user_id != exclude_user, s.cases.c.status == "analyzed")
        )
        out: dict[str, dict] = {}
        revoked = self.revoked_users()
        with self.engine.connect() as conn:
            for r in conn.execute(q):
                if r.tg_user_id in revoked:
                    continue
                if any(distance(r.phash, h) <= max_distance for h in mine):
                    out[r.case_id] = {"user": r.tg_user_id, "level": r.level}
        return out

    def case_indicator_pairs(self, case_ids: Iterable[str], kinds: set[str]) -> dict[str, set]:
        ids = list(case_ids)
        if not ids:
            return {}
        q = (
            select(s.case_indicators.c.case_id, s.indicators.c.kind, s.indicators.c.hmac)
            .select_from(s.case_indicators.join(s.indicators))
            .where(s.case_indicators.c.case_id.in_(ids), s.indicators.c.kind.in_(kinds))
        )
        out: dict[str, set] = {}
        with self.engine.connect() as conn:
            for r in conn.execute(q):
                out.setdefault(r.case_id, set()).add((r.kind, r.hmac))
        return out

    # --- Modus operandi ----------------------------------------------------------------------

    def save_features(self, features: dict) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(insert(s.case_features).values(**features))
            return int(res.inserted_primary_key[0])

    # --- Follow-up («¿qué pasó al final?») ----------------------------------------------------

    def add_followup(self, case_id: str, feature_id: int | None, lang: str = "es") -> None:
        with self.engine.begin() as conn:
            conn.execute(
                insert(s.followups).values(
                    case_id=case_id, feature_id=feature_id, created_at=utcnow(), lang=lang
                )
            )

    def due_followups(self, *, min_age: dt.timedelta, max_age: dt.timedelta) -> list[dict]:
        """Unasked follow-ups of analysed cases aged between min_age and max_age, newest first."""
        now = utcnow()
        f, c = s.followups, s.cases
        q = (
            select(f.c.case_id, c.c.tg_user_id, c.c.level, c.c.created_at, f.c.lang)
            .select_from(f.join(c, f.c.case_id == c.c.id))
            .where(
                f.c.asked_at.is_(None),
                c.c.status == "analyzed",
                c.c.level.is_not(None),
                c.c.created_at <= now - min_age,
                c.c.created_at >= now - max_age,
            )
            .order_by(c.c.created_at.desc())
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(q)]

    def mark_followups_asked(self, case_ids: Iterable[str], *, skipped: bool = False) -> None:
        """Asked, or (skipped=True) settled without asking: closed, it takes no answer."""
        ids = list(case_ids)
        if not ids:
            return
        now = utcnow()
        values = {"asked_at": now, "answered_at": now} if skipped else {"asked_at": now}
        with self.engine.begin() as conn:
            conn.execute(update(s.followups).where(s.followups.c.case_id.in_(ids)).values(**values))

    def record_outcome(self, case_id: str, tg_user_id: int, outcome: str) -> str | None:
        """Store the answer once, only from the case's owner. Returns the case's level, or None
        when it does not apply (not theirs, already answered, never asked)."""
        if outcome not in ("scam", "legit", "unknown"):
            return None
        f, c = s.followups, s.cases
        with self.engine.begin() as conn:
            row = conn.execute(
                select(f.c.feature_id, f.c.asked_at, f.c.answered_at, c.c.tg_user_id, c.c.level)
                .select_from(f.join(c, f.c.case_id == c.c.id))
                .where(f.c.case_id == case_id)
            ).first()
            if (
                row is None
                or row.tg_user_id != tg_user_id
                or row.asked_at is None
                or row.answered_at is not None
            ):
                return None
            conn.execute(update(f).where(f.c.case_id == case_id).values(answered_at=utcnow()))
            if row.feature_id is not None:
                conn.execute(
                    insert(s.feature_outcomes).values(
                        feature_id=row.feature_id,
                        outcome=outcome,
                        answered_day=utcnow().date().isoformat(),
                    )
                )
            return row.level

    def outcome_counts(self, since_day: str | None = None) -> dict[tuple[str, str], int]:
        """(level reported, outcome) → count, from the anonymous features."""
        o, cf = s.feature_outcomes, s.case_features
        q = (
            select(cf.c.level, o.c.outcome, func.count())
            .select_from(o.join(cf, o.c.feature_id == cf.c.id))
            .group_by(cf.c.level, o.c.outcome)
        )
        if since_day:
            q = q.where(o.c.answered_day >= since_day)
        with self.engine.connect() as conn:
            return {(r[0], r[1]): int(r[2]) for r in conn.execute(q)}

    def keep_for_research(self, case_id: str) -> bool:
        """Copy a case's redacted record into the research archive, without the user id.
        False if the case is gone or already purged."""
        row = self.get_case(case_id)
        if not row or not row["case_json"]:
            return False
        with self.engine.begin() as conn:
            conn.execute(
                insert(s.research_cases).values(
                    kept_at=utcnow(),
                    level=row["level"],
                    signal_codes=row["signal_codes"],
                    case_json=row["case_json"],
                )
            )
        return True

    # --- Members and invites (invite-only beta) --------------------------------------------

    def member(self, tg_user_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(s.members).where(s.members.c.tg_user_id == tg_user_id)
            ).first()
        return dict(row._mapping) if row else None

    def is_member(self, tg_user_id: int) -> bool:
        m = self.member(tg_user_id)
        return bool(m and m["revoked_at"] is None)

    def join_public(self, tg_user_id: int) -> bool:
        """Open access: a stranger becomes a public member (small quota, one shared branch of
        the trust tree, no seat). False if they already have a row (a revoked user stays out)."""
        with self.engine.begin() as conn:
            if conn.execute(
                select(s.members.c.tg_user_id).where(s.members.c.tg_user_id == tg_user_id)
            ).first():
                return False
            conn.execute(
                insert(s.members).values(
                    tg_user_id=tg_user_id, joined_at=utcnow(), invites_left=0, campaign=PUBLIC
                )
            )
        return True

    def revoked_users(self) -> set[int]:
        with self.engine.connect() as conn:
            q = select(s.members.c.tg_user_id).where(s.members.c.revoked_at.is_not(None))
            return {r.tg_user_id for r in conn.execute(q)}

    def beta_seats_taken(self, conn=None, *, idle_days: int = 30) -> int:
        """Active members (analysed or joined in the last idle_days, not revoked) plus pending
        (unused, unexpired) invites and the unused uses of open campaigns. Idle members keep
        access but free their seat."""
        now = utcnow()
        m = s.members
        members = (
            select(func.count())
            .select_from(m)
            .where(
                m.c.revoked_at.is_(None),
                or_(m.c.campaign.is_(None), m.c.campaign != PUBLIC),
                func.coalesce(m.c.last_active_at, m.c.joined_at)
                >= now - dt.timedelta(days=idle_days),
            )
        )
        pending = (
            select(func.count())
            .select_from(s.invites)
            .where(s.invites.c.used_by.is_(None), s.invites.c.expires_at >= now)
        )
        c = s.campaigns
        held = select(func.coalesce(func.sum(c.c.max_uses - c.c.uses), 0)).where(
            c.c.closed_at.is_(None), c.c.expires_at >= now
        )
        if conn is None:
            with self.engine.connect() as cn:
                return self.beta_seats_taken(cn, idle_days=idle_days)
        return int(sum(conn.execute(q).scalar_one() for q in (members, pending, held)))

    def create_invite(
        self,
        creator: int,
        *,
        ttl_days: int,
        unlimited: bool,
        max_members: int | None = None,
        idle_days: int = 30,
    ) -> str | None:
        """A one-use code, or None when the creator has no invites left or the beta is full
        (members + pending invites >= max_members). Roots have no per-person limit."""
        import secrets

        code = secrets.token_urlsafe(9)[:12]
        now = utcnow()
        with self.engine.begin() as conn:
            if (
                max_members is not None
                and self.beta_seats_taken(conn, idle_days=idle_days) >= max_members
            ):
                return None
            if not unlimited:
                res = conn.execute(
                    update(s.members)
                    .where(
                        s.members.c.tg_user_id == creator,
                        s.members.c.revoked_at.is_(None),
                        s.members.c.invites_left > 0,
                    )
                    .values(invites_left=s.members.c.invites_left - 1)
                )
                if res.rowcount != 1:
                    return None
            conn.execute(
                insert(s.invites).values(
                    code=code,
                    created_by=creator,
                    created_at=now,
                    expires_at=now + dt.timedelta(days=ttl_days),
                )
            )
        return code

    def create_campaign(
        self,
        creator: int,
        name: str,
        *,
        uses: int,
        ttl_days: int,
        max_members: int | None = None,
        idle_days: int = 30,
    ) -> str | None:
        """A group invite: one code that `uses` people can redeem. None when the name is taken
        or its uses do not fit in the free beta seats."""
        import secrets

        code = secrets.token_urlsafe(9)[:12]
        now = utcnow()
        with self.engine.begin() as conn:
            if conn.execute(select(s.campaigns).where(s.campaigns.c.name == name)).first():
                return None
            if (
                max_members is not None
                and self.beta_seats_taken(conn, idle_days=idle_days) + uses > max_members
            ):
                return None
            conn.execute(
                insert(s.campaigns).values(
                    code=code,
                    name=name,
                    created_by=creator,
                    created_at=now,
                    expires_at=now + dt.timedelta(days=ttl_days),
                    max_uses=uses,
                )
            )
        return code

    def close_campaign(self, name: str) -> bool:
        """Stop a group invite now: its unused uses free their seats."""
        with self.engine.begin() as conn:
            res = conn.execute(
                update(s.campaigns)
                .where(s.campaigns.c.name == name, s.campaigns.c.closed_at.is_(None))
                .values(closed_at=utcnow())
            )
        return res.rowcount == 1

    def campaigns_list(self) -> list[dict]:
        """Campaigns with how many joined and how many analyses their members ran."""
        c, m = s.campaigns, s.members
        analyses = (
            select(func.coalesce(func.sum(m.c.analyses_used), 0))
            .where(m.c.campaign == c.c.code)
            .scalar_subquery()
        )
        with self.engine.connect() as conn:
            rows = conn.execute(select(c, analyses.label("analyses")).order_by(c.c.created_at))
            return [dict(r._mapping) for r in rows]

    def _redeem_campaign(
        self, conn, code: str, tg_user_id: int, now: dt.datetime, *, invites_if_used: int
    ) -> bool:
        c = s.campaigns
        camp = conn.execute(select(c).where(c.c.code == code)).first()
        if camp is None or camp.closed_at is not None or camp.expires_at < now:
            return False
        res = conn.execute(
            update(c).where(c.c.code == code, c.c.uses < c.c.max_uses).values(uses=c.c.uses + 1)
        )
        if res.rowcount != 1:
            return False
        # No invites of their own until their first analysis (record_usage), unless they are
        # a public member who already ran one.
        self._put_member(
            conn,
            tg_user_id,
            invited_by=camp.created_by,
            joined_at=now,
            invites_left=invites_if_used,
            campaign=code,
        )
        return True

    def redeem_invite(self, code: str, tg_user_id: int, *, invites_for_new: int) -> bool:
        """Join with a code: a personal invite (unused, not expired, from a creator who is still
        in) or a campaign (open, unexpired, uses left). Atomic."""
        now = utcnow()
        with self.engine.begin() as conn:
            row = conn.execute(
                select(s.members).where(s.members.c.tg_user_id == tg_user_id)
            ).first()
            # Already in, or revoked (a new code does not bring them back). A public member
            # can still redeem: the invite upgrades them.
            if row is not None and (row.campaign != PUBLIC or row.revoked_at is not None):
                return False
            inv = conn.execute(select(s.invites).where(s.invites.c.code == code)).first()
            if inv is None:
                used = row is not None and row.analyses_used > 0
                return self._redeem_campaign(
                    conn, code, tg_user_id, now, invites_if_used=invites_for_new if used else 0
                )
            if inv.used_by is not None or inv.expires_at < now:
                return False
            creator = conn.execute(
                select(s.members.c.revoked_at).where(s.members.c.tg_user_id == inv.created_by)
            ).first()
            if creator is not None and creator.revoked_at is not None:
                return False
            res = conn.execute(
                update(s.invites)
                .where(s.invites.c.code == code, s.invites.c.used_by.is_(None))
                .values(used_by=tg_user_id, used_at=now)
            )
            if res.rowcount != 1:
                return False
            self._put_member(
                conn,
                tg_user_id,
                invited_by=inv.created_by,
                joined_at=now,
                invites_left=invites_for_new,
                campaign=None,
            )
        return True

    @staticmethod
    def _put_member(conn, tg_user_id: int, **values) -> None:
        """Insert a member, or upgrade the public member they were (keeping their usage)."""
        m = s.members
        res = conn.execute(update(m).where(m.c.tg_user_id == tg_user_id).values(**values))
        if res.rowcount == 0:
            conn.execute(insert(m).values(tg_user_id=tg_user_id, **values))

    def set_member(
        self,
        tg_user_id: int,
        *,
        revoked: bool | None = None,
        invites_left: int | None = None,
        quota: int | None = None,
    ) -> bool:
        values: dict = {}
        if quota is not None:
            values["quota"] = quota
        if revoked is not None:
            values["revoked_at"] = utcnow() if revoked else None
        if invites_left is not None:
            values["invites_left"] = invites_left
        with self.engine.begin() as conn:
            res = conn.execute(
                update(s.members).where(s.members.c.tg_user_id == tg_user_id).values(**values)
            )
        return res.rowcount == 1

    def members_list(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(select(s.members).order_by(s.members.c.joined_at))
            ]

    def branches(self, users: Iterable[int], roots: frozenset[int]) -> dict[int, int | str]:
        """User → branch: the ancestor just below a root (or the user, if a root invited them,
        if they are a root, or if nobody did). Everyone who came through one campaign, and
        their invitees, is one branch. Two reports from one branch count once."""
        m = s.members
        with self.engine.connect() as conn:
            rows = conn.execute(select(m.c.tg_user_id, m.c.invited_by, m.c.campaign)).all()
        parent = {r.tg_user_id: r.invited_by for r in rows}
        campaign = {r.tg_user_id: r.campaign for r in rows if r.campaign}
        out: dict[int, int | str] = {}
        for u in users:
            if is_web_user(u):
                # One branch, however many IPs: a few addresses must not make a «seen before».
                out[u] = WEB_BRANCH
                continue
            cur, seen = u, set()
            while parent.get(cur) is not None and parent[cur] not in roots and cur not in seen:
                seen.add(cur)
                cur = parent[cur]
            out[u] = f"campaign:{campaign[cur]}" if cur in campaign else cur
        return out

    # --- Disputes (a landlord asks us to stop using an identifier) -------------------------

    def dispute(self, kind: str, digest: str, note: str = "") -> None:
        t = s.disputes
        with self.engine.begin() as conn:
            conn.execute(delete(t).where(t.c.kind == kind, t.c.hmac == digest))
            conn.execute(
                insert(t).values(kind=kind, hmac=digest, created_at=utcnow(), note=note or None)
            )

    def disputed(self) -> set[tuple[str, str]]:
        with self.engine.connect() as conn:
            return {(r.kind, r.hmac) for r in conn.execute(select(s.disputes))}

    # --- Usage (rate limits) ----------------------------------------------------------------

    def record_usage(self, tg_user_id: int, *, campaign_invites: int = 0) -> None:
        """One analysis. A member who came through a campaign gets campaign_invites invites of
        their own with their first analysis."""
        now = utcnow()
        m = s.members
        with self.engine.begin() as conn:
            conn.execute(insert(s.usage).values(tg_user_id=tg_user_id, created_at=now))
            # Beta budget of members (roots are not rows: nothing happens for them).
            first_from_campaign = (
                m.c.campaign.is_not(None) & (m.c.campaign != PUBLIC) & (m.c.analyses_used == 0)
            )
            conn.execute(
                update(m)
                .where(m.c.tg_user_id == tg_user_id)
                .values(
                    analyses_used=m.c.analyses_used + 1,
                    last_active_at=now,
                    invites_left=case(
                        (first_from_campaign, campaign_invites), else_=m.c.invites_left
                    ),
                )
            )

    def count_usage_since(
        self, since: dt.datetime, tg_user_id: int | None = None, *, web: bool = False
    ) -> int:
        """Analyses since a time: everyone's, one user's, or (web) all website visitors'."""
        q = select(func.count()).select_from(s.usage).where(s.usage.c.created_at >= since)
        if tg_user_id is not None:
            q = q.where(s.usage.c.tg_user_id == tg_user_id)
        if web:
            q = q.where(s.usage.c.tg_user_id < 0)
        with self.engine.connect() as conn:
            return int(conn.execute(q).scalar_one())

    # --- Retention -------------------------------------------------------------------------

    def purge_expired(self, now: dt.datetime | None = None) -> int:
        now = now or utcnow()
        t = s.cases
        with self.engine.begin() as conn:
            res = conn.execute(
                update(t)
                .where(t.c.expires_at < now, t.c.purged_at.is_(None))
                .values(case_json=None, verdict_json=None, report_text=None, purged_at=now)
            )
            conn.execute(delete(s.usage).where(s.usage.c.created_at < now - dt.timedelta(days=7)))
            return res.rowcount

    def chosen_lang(self, tg_user_id: int) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(
                select(s.user_prefs.c.lang).where(s.user_prefs.c.tg_user_id == tg_user_id)
            ).scalar_one_or_none()

    def choose_lang(self, tg_user_id: int, lang: str) -> None:
        t = s.user_prefs
        with self.engine.begin() as conn:
            conn.execute(delete(t).where(t.c.tg_user_id == tg_user_id))
            conn.execute(insert(t).values(tg_user_id=tg_user_id, lang=lang, updated_at=utcnow()))

    def delete_user(self, tg_user_id: int) -> int:
        """Delete every case of a user (/borrar), and indicators no other case uses."""
        with self.engine.begin() as conn:
            conn.execute(delete(s.user_prefs).where(s.user_prefs.c.tg_user_id == tg_user_id))
            res = conn.execute(delete(s.cases).where(s.cases.c.tg_user_id == tg_user_id))
            used = select(s.case_indicators.c.indicator_id)
            conn.execute(delete(s.indicators).where(s.indicators.c.id.not_in(used)))
            return res.rowcount
