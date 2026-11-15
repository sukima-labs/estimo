"""Command line entry point: `mikiwame <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _check_config(_: argparse.Namespace) -> int:
    from mikiwame.config import load_settings

    settings = load_settings()
    for (role, fallback), ref in sorted(settings.models.items()):
        kind = "fallback" if fallback else "primary"
        print(f"{role:12} {kind:8} {ref.label} ({ref.model.protocol})")
    print(f"instance: {settings.instance}  database: {settings.database_url}")
    print(f"allowed users: {len(settings.allowed_users)}  admin: {bool(settings.admin_chat_id)}")
    return 0


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )
    # httpx logs every request URL at INFO, and Bot API URLs carry the token.
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _web(_: argparse.Namespace) -> int:
    from mikiwame.config import load_settings
    from mikiwame.web.app import run

    _setup_logging()
    run(load_settings())
    return 0


def _bot(_: argparse.Namespace) -> int:
    from mikiwame.bot.app import run
    from mikiwame.config import load_settings

    _setup_logging()
    run(load_settings())
    return 0


def _alert(args: argparse.Namespace) -> int:
    """Works even when the config is broken (that may be why the bot is down): it only needs
    the host gate or the kChat webhook, or else the bot token and the admin chat id from the
    env files. Logs where the alert landed (host gate / kChat), so the OnFailure units' journal
    shows it."""
    from dotenv import dotenv_values

    _setup_logging()

    from mikiwame.bot.alert import kchat_webhook, notify_gate, send_admin
    from mikiwame.config import env_files

    values: dict[str, str | None] = {}
    for f in env_files():
        values.update(dotenv_values(f))
    values.update({k: v for k, v in os.environ.items() if k.startswith(("MIKIWAME_", "TELEGRAM_"))})
    token, admin = values.get("TELEGRAM_BOT_TOKEN"), values.get("MIKIWAME_ADMIN_CHAT_ID")
    if not notify_gate() and not kchat_webhook() and (not token or not admin):
        print("alert: missing TELEGRAM_BOT_TOKEN or MIKIWAME_ADMIN_CHAT_ID", file=sys.stderr)
        return 1
    instance = values.get("MIKIWAME_INSTANCE") or "default"
    ok = send_admin(
        token or "",
        int(admin) if admin else None,
        " ".join(args.text),
        label=f"Mikiwame ({instance})",
    )
    return 0 if ok else 1


def _purge(_: argparse.Namespace) -> int:
    from mikiwame.config import load_settings
    from mikiwame.store.sql import Store

    print(f"purged {Store(load_settings().database_url).purge_expired()} cases")
    return 0


def _stats(args: argparse.Namespace) -> int:
    import datetime as dt

    from mikiwame.config import load_settings
    from mikiwame.digest import spend_by_model, usage_by_level
    from mikiwame.store.sql import Store

    s = load_settings()
    store = Store(s.database_url)
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=args.days)
    print(f"last {args.days} day(s)")
    for level, n, avg, mx in usage_by_level(store, since):
        avg_eur, max_eur = (avg or 0) * s.usd_eur, (mx or 0) * s.usd_eur
        label = level or "failed"
        print(f"  {label:7} n={n:4}  cost/report avg {avg_eur:.4f} EUR  max {max_eur:.4f} EUR")
    for role, model, n, cost, failed in spend_by_model(store, since):
        print(f"  {role:12} {model:22} calls={n:4} failed={failed:3} ${cost:.4f}")
    return 0


def _patterns(args: argparse.Namespace) -> int:
    import datetime as dt

    from mikiwame.config import load_settings
    from mikiwame.digest import feature_rows
    from mikiwame.patterns import summarize
    from mikiwame.store.sql import Store

    store = Store(load_settings().database_url)
    print(summarize(feature_rows(store, dt.date.today() - dt.timedelta(days=args.days))))
    return 0


def _or_dash(n: int | None) -> str:
    return "-" if n is None else str(n)


def _member(args: argparse.Namespace) -> int:
    from mikiwame.config import load_settings
    from mikiwame.limits import member_quota
    from mikiwame.store.sql import Store

    s = load_settings()
    store = Store(s.database_url)
    if args.action == "list":
        print(f"roots (env allowlist): {sorted(s.allowed_users)}")
        for m in store.members_list():
            state = f"revoked {m['revoked_at']:%Y-%m-%d}" if m["revoked_at"] else "active"
            print(
                f"  {m['tg_user_id']}  invited_by={m['invited_by']}  "
                f"joined={m['joined_at']:%Y-%m-%d}  invites_left={m['invites_left']}  "
                f"used={m['analyses_used']}/{_or_dash(member_quota(s, m))}  "
                f"via={m['campaign'] or 'invite'}  {state}"
            )
        return 0
    if args.user is None:
        print("member: this action needs a user id", file=sys.stderr)
        return 2
    if args.action == "revoke":
        ok = store.set_member(args.user, revoked=True)
    elif args.action == "restore":
        ok = store.set_member(args.user, revoked=False)
    elif args.action == "quota":
        ok = store.set_member(args.user, quota=args.n)
    else:  # invites
        ok = store.set_member(args.user, invites_left=args.n)
    print("ok" if ok else "no such member")
    return 0 if ok else 1


def _bot_username(token: str) -> str | None:
    import httpx

    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        return r.json()["result"]["username"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _campaign(args: argparse.Namespace) -> int:
    """Group invites: one link with a few uses to post in a Telegram or WhatsApp group."""
    from mikiwame.config import load_settings
    from mikiwame.store.sql import Store

    s = load_settings()
    store = Store(s.database_url)
    if args.action == "list":
        for c in store.campaigns_list():
            state = (
                f"closed {c['closed_at']:%Y-%m-%d}"
                if c["closed_at"]
                else (f"until {c['expires_at']:%Y-%m-%d}")
            )
            print(
                f"  {c['name']}  joined={c['uses']}/{c['max_uses']}  "
                f"analyses={c['analyses']}  {state}"
            )
        return 0
    if not args.name:
        print("campaign: this action needs a name", file=sys.stderr)
        return 2
    if args.action == "close":
        ok = store.close_campaign(args.name)
        print("ok" if ok else "no such open campaign")
        return 0 if ok else 1
    if not s.allowed_users:
        print("campaign: no root in MIKIWAME_ALLOWED_USERS to own it", file=sys.stderr)
        return 2
    code = store.create_campaign(
        min(s.allowed_users),
        args.name,
        uses=args.uses,
        ttl_days=args.days,
        max_members=s.max_members,
        idle_days=s.member_idle_days,
    )
    if code is None:
        seats = store.beta_seats_taken(idle_days=s.member_idle_days)
        print(f"refused: name taken, or {args.uses} uses do not fit ({seats}/{s.max_members})")
        return 1
    username = _bot_username(s.telegram_bot_token.get_secret_value()) or "<bot>"
    print(f"https://t.me/{username}?start=inv_{code}")
    return 0


def _dispute(args: argparse.Namespace) -> int:
    """A landlord says an identifier of theirs is in the registry by mistake: never count it
    again in recidivism. Only the HMAC is stored, with a short note (no personal data)."""
    from mikiwame.config import load_settings
    from mikiwame.indicators import digest, indicator_value
    from mikiwame.store.sql import Store

    s = load_settings()
    value = indicator_value(args.kind, args.value)
    if not value:
        print(f"dispute: cannot read a {args.kind} in that value", file=sys.stderr)
        return 2
    key = s.hmac_key.get_secret_value().encode()
    Store(s.database_url).dispute(args.kind, digest(key, args.kind, value), " ".join(args.note))
    print(f"disputed {args.kind} (normalised as {value!r}); it no longer counts in recidivism")
    return 0


def _digest(args: argparse.Namespace) -> int:
    from mikiwame.bot.alert import send_admin
    from mikiwame.config import load_settings
    from mikiwame.digest import build

    s = load_settings()
    body = build(s, days=args.days)
    if not args.send:
        print(body)
        return 0
    _setup_logging()  # so the journal says which way it went (host gate, kChat, Telegram)
    label = f"Mikiwame ({os.environ.get('MIKIWAME_INSTANCE') or 'default'})"
    token = s.telegram_bot_token.get_secret_value()
    ok = send_admin(token, s.admin_chat_id, body, label=label, icon="📬")
    # Aggregates only, but the journal gets just the size: enough to check it went out.
    print(f"digest: {len(body)} characters, {'delivered' if ok else 'NOT delivered'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mikiwame")
    parser.add_argument(
        "--instance",
        help="bot instance (loads .env.<instance> over .env); default: $MIKIWAME_INSTANCE",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate settings and model privacy").set_defaults(
        func=_check_config
    )
    sub.add_parser("bot", help="run the Telegram bot (long polling)").set_defaults(func=_bot)
    sub.add_parser("web", help="run the website (HTTP, behind the relay)").set_defaults(func=_web)
    p = sub.add_parser("alert", help="send a message to the admin chat")
    p.add_argument("text", nargs="+")
    p.set_defaults(func=_alert)
    sub.add_parser("purge", help="apply the retention policy now").set_defaults(func=_purge)
    p = sub.add_parser("patterns", help="modus operandi summary from anonymous case features")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=_patterns)
    p = sub.add_parser("stats", help="reports and LLM spend")
    p.add_argument("--days", type=int, default=1)
    p.set_defaults(func=_stats)
    p = sub.add_parser("member", help="invite-only beta: list, revoke, restore, set invites")
    p.add_argument("action", choices=["list", "revoke", "restore", "invites", "quota"])
    p.add_argument("user", type=int, nargs="?")
    p.add_argument("n", type=int, nargs="?", default=0, help="invites left, or beta quota")
    p.set_defaults(func=_member)
    p = sub.add_parser("campaign", help="group invites: create, list, close")
    p.add_argument("action", choices=["create", "list", "close"])
    p.add_argument("name", nargs="?", help="short label of the group, e.g. alquilaenmadrid")
    p.add_argument("--uses", type=int, default=10)
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=_campaign)
    p = sub.add_parser("dispute", help="a landlord's identifier stops counting in recidivism")
    p.add_argument("kind", choices=["phone", "iban", "email", "name", "business", "listing"])
    p.add_argument("value")
    p.add_argument("note", nargs="*", help="short, no personal data (e.g. 'correo 02-10')")
    p.set_defaults(func=_dispute)

    p = sub.add_parser("digest", help="weekly digest for the admin (prints it; --send sends it)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--send", action="store_true", help="send it to the admin chat via the bot")
    p.set_defaults(func=_digest)
    args = parser.parse_args(argv)
    if args.instance:
        os.environ["MIKIWAME_INSTANCE"] = args.instance
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
