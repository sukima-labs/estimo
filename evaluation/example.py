"""Run the real analysis on the invented example case and save it for the bot and the website.

Newcomers who have no case at hand see what a report looks like («Ver un ejemplo») without
spending an analysis. The report is the pipeline's own output, not written by hand.

Spends a little of the house quota (two text analyses). Run it on the test instance, and again
when the report's wording changes:

    MIKIWAME_INSTANCE=pruebas uv run python -m evaluation.example
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json

from mikiwame.config import load_settings
from mikiwame.example import CASE, DATA_FILE
from mikiwame.extract.build import Draft, DraftItem
from mikiwame.llm.client import LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.pipeline import Analyzer
from mikiwame.store.market import MarketStore
from mikiwame.store.sql import Store

# Not a Telegram user nor a web visitor id (those are HMACs of 6 bytes): its cases are deleted
# right after.
USER_ID = -424242


async def main() -> None:
    s = load_settings()
    store = Store(s.database_url)
    client = LLMClient(timeout_s=s.llm_timeout_s, env=s.provider_env())
    router = LLMRouter(s, store, client)
    analyzer = Analyzer(s, store, router, MarketStore(s.market_database_url), None)
    out: dict = {"generated": dt.date.today().isoformat(), "level": {}, "report": {}}
    try:
        for lang, text in CASE.items():
            draft = Draft(user_id=USER_ID)
            draft.items.append(DraftItem(kind="text", text=text))
            result = await analyzer.analyze_draft(draft, lang)
            out["level"][lang] = result.verdict.level.key
            out["report"][lang] = result.report
            print(f"{lang}: {result.verdict.level.key}")
    finally:
        store.delete_user(USER_ID)
    DATA_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"written {DATA_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
