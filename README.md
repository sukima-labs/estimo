# estimo (¿Es timo? · Mikiwame)

**Closed on 15 November 2026.** A Sukima Labs concept project. It received a rental listing and
the chat with the «landlord» and returned a scam-risk report with a green / amber / red light and
the reason for each signal. It never said anyone *is* a scammer: it said a pattern appears in
known scams.

- Archive site (guides ES+EN, example report): <https://sukimalabs.com/estimo/>
- Post-mortem (Spanish): [POSTMORTEM.md](POSTMORTEM.md)
- Licence: MIT

## What is in here

| Path | What |
|---|---|
| `src/mikiwame/extract/` | text and screenshot (vision) → structured case; redaction before anything is stored |
| `src/mikiwame/voters/` | independent voters: phone, IBAN, text, fees, street, market price, recidivism |
| `src/mikiwame/adversarial.py` | a second model tries to overturn the preliminary verdict |
| `src/mikiwame/aggregate.py`, `rubric.py` | weights, hard rules, the signal catalogue (ES/EN) |
| `src/mikiwame/llm/` | provider client, model registry with a privacy policy, router with a budget |
| `src/mikiwame/bot/`, `web/` | Telegram bot and website (the same analysis) |
| `evaluation/` | measures red precision first (a legitimate ad in red is the worst failure) |
| `tests/fixtures/` | 37 invented cases, ES, with synthetic screenshots; one is a prompt-injection case |
| `docs/` | the static archive site (built by `tools/build_site.py`) |

Design rules worth keeping: never mark a legitimate ad red (precision before recall), all input
is written by the potential scammer and must not change the verdict by instructions, personal
data only as keyed HMAC, and informative signals that never move the verdict.

## Running it

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest          # no network: the model calls are mocked
uv run ruff check
cp .env.example .env   # then fill it in; `uv run mikiwame check-config` validates it
```

The listing mirror (`MIKIWAME_MARKET_DATABASE_URL`) is optional and no sync client ships here:
without it the market voter abstains. The model policy in `llm/registry.py` encodes the
decisions of the original deployment; read it before pointing the project at another provider.

## The site

`uv run python tools/build_site.py` regenerates `docs/` from `web/estimo/` and
`src/mikiwame/example.json`. GitHub Pages serves `docs/`.
