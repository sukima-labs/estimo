"""Everything the bot says, in English (14-10-2026). Same names as texts.py; tests/test_i18n.py
checks that none is missing."""

HELP = """Hi, I'm <b>Mikiwame</b> («¿Es timo?» means «Is it a scam?»). I help you spot rental \
scams <b>before</b> you pay anything.

Send me whatever you have, ideally starting with the link to the listing:
• the link (or the text) of the listing,
• screenshots of your chat with the advertiser (WhatsApp, Telegram, the portal's chat),
• photos of the property they sent you: I compare them with other listings,
• the phone number, IBAN or name they gave you.

When you have everything, press <b>Analyse</b>. I reply with a traffic light 🟢 🟠 🔴, the \
reasons, and what to check. No case yet? See an example: /example

/new start another analysis · /privacy what I keep · /delete delete your data · \
/invite invite someone · /espanol en español"""

PRIVACY = """<b>What I keep, and for how long</b>
• Screenshots are not kept: they are read for the analysis and discarded.
• The text of the case and the report are kept for {days} days and then deleted.
• The advertiser's phone numbers, IBANs and names are kept only as an irreversible \
fingerprint (HMAC), to recognise listings that repeat.
• The analysis is done by an OpenAI AI model, through OpenCode. It does not train on this \
data, but OpenAI keeps a log of requests for up to 30 days to detect abuse.
• To check that the listing's street exists, I look up the street name and the town \
(no number or floor) on OpenStreetMap.
• To apply usage limits I keep how many analyses you ask for and when, for 7 days, and your \
language.
• For each case I keep, with no expiry, an anonymous record (signals, channel, country of the \
account, amounts) to understand how scams work. The conversation is only kept beyond {days} \
days if you press «Save anonymised to help learn», and then without your user.
• /delete removes all your cases.

<b>Controller and contact</b>: Patricio Llorens, info@patriciollorens.com, to exercise your data \
protection rights or to complain. If you are a landlord and think your details are in our \
registry by mistake, write to us: we review it and stop using them."""

CLOSED = (
    "Mikiwame is in a private test: you join with an invite from someone who already uses it. "
    "Thanks for your interest. Contact: info@patriciollorens.com"
)
PUBLIC_WELCOME = "It's free. For safety, each person can run up to {daily} analyses a day."
PUBLIC_WELCOME_QUOTA = (
    "You can run {n} analyses for free. If someone who already uses Mikiwame invites you, you "
    "get more."
)
INVITE_CREATED = (
    "🎟 Invite for one person, valid for {days} days. Send them this link:\n{link}\n\n"
    "Only invite people you know: what your invitees analyse counts together with yours in the "
    "registry.{left}"
)
INVITE_LEFT = "\nYou have {n} invite(s) left."
INVITE_SHARE = (
    "You don't need an invite to bring someone: send them this link, it's free (up to {daily} "
    "analyses a day).\n{link}"
)
INVITE_SHARE_QUOTA = (
    "You don't need an invite to bring someone: send them this link and they get {n} free "
    "analyses.\n{link}"
)
INVITE_NONE_LEFT = "You have no invites left. If you need more, write to info@patriciollorens.com."
BETA_FULL = (
    "The beta is full for now: no more invites can be created. Thanks for wanting to bring someone."
)
INVITE_WELCOME = "🎟 Invite accepted. Welcome to Mikiwame!"
INVITE_INVALID = (
    "That invite is not valid: it was already used, it has no places left, it expired or it "
    "does not exist. Ask whoever sent it for another one."
)
DRAFT_STATUS = "📥 I have {what}. When you have everything, press <b>Analyse</b>."
ASK_LINK = (
    "🔗 If you have the <b>link to the listing</b>, send it too: I record it to recognise "
    "repeated or copied listings."
)
DRAFT_STATUS_CONTINUE = (
    "📥 I'm adding it to the previous case: I now have {what} in total. Press <b>Analyse</b> to "
    "look at it all together, or <b>Start over</b> if it is another listing."
)
NOT_CHARGED = "This attempt has not used up any analysis."
UNKNOWN_LINK_AGAIN = (
    "🔗 With just that link I still have nothing to assess. Add <b>screenshots of the "
    "listing</b> and <b>of the conversation with the advertiser</b>, and press <b>Analyse</b> "
    "again. " + NOT_CHARGED
)
EMPTY_DRAFT = (
    "You haven't sent me anything yet. Send me screenshots of the chat or the text of the listing."
)
ANALYZING = "🔎 Analysing… it usually takes less than a minute."
BUSY = "I'm still analysing your previous case; wait for it to finish."
TOO_MANY_IMAGES = "I analyse at most {n} screenshots per case; I will ignore the rest."
UNSUPPORTED = "I can only analyse text and screenshots (images)."
RESET = "OK, starting from scratch. Send me the new one."
DELETE_CONFIRM = "Are you sure you want to delete all your cases? This cannot be undone."
DELETED = "Done: I deleted {n} case(s) of yours."
DELETE_CANCELLED = "I haven't deleted anything."
DELETE_BUSY = "Wait for the analysis in progress to finish and press /delete again."
ERROR = "Something went wrong on my side. Please try again in a while."
BUTTON_ANALYZE = "🔎 Analyse"
BUTTON_RESET = "🗑 Start over"
BUTTON_NEW_CASE = "🆕 Analyse another listing"
BUTTON_KEEP = "📚 Save anonymised to help learn"
KEPT = (
    "Thank you. I saved this case anonymised (without your user or personal data) to learn to "
    "recognise new kinds of scam."
)
KEEP_FAILED = "This case is no longer available to save."
BUTTON_YES = "Yes, delete"
BUTTON_NO = "No"
BUTTON_EXAMPLE = "👀 See an example"
EXAMPLE_INTRO = (
    "<b>Invented example</b> (the details belong to nobody). Imagine someone sends me this:\n\n"
    "<i>{case}</i>"
)
EXAMPLE_OUTRO = (
    "That is what a report looks like. When you have a case of your own, send me the listing "
    "and the conversation (text or screenshots) and press <b>Analyse</b>. Viewing the example "
    "does not count as an analysis."
)
LANG_SET = "OK, I'll speak English from now on. /espanol to switch back."


def what(images: int, texts: int) -> str:
    parts = []
    if images:
        parts.append(f"{images} screenshot" + ("s" if images != 1 else ""))
    if texts:
        parts.append(f"{texts} text" + ("s" if texts != 1 else ""))
    return " and ".join(parts) or "nothing"


FOLLOWUP = (
    "🔁 A few days ago you analysed a listing with me ({level}, on {day}). What happened in the "
    "end? Your answer is anonymous and helps us get it right more often."
)
FOLLOWUP_LEVEL = {
    "red": "🔴 high risk",
    "amber": "🟠 caution",
    "green": "🟢 no clear signs",
}
BUTTON_OUT_SCAM = "It was a scam"
BUTTON_OUT_LEGIT = "It was genuine"
BUTTON_OUT_UNKNOWN = "Didn't go ahead / don't know"
FOLLOWUP_THANKS = "Thanks, noted. 🙏"
FOLLOWUP_THANKS_SORRY = "Thanks, and sorry for the false alarm: we use it to fix the analysis. 🙏"
FOLLOWUP_THANKS_MISSED = (
    "Thanks for telling us, and we are sorry: we use it so it does not slip past us again. If "
    "you paid, keep the receipt and tell your bank and the police as soon as you can."
)
FOLLOWUP_DONE = "I already had your answer. Thanks!"
