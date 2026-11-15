"""English for the signal catalogue (rubric.py), by code. Decided 14-10-2026 (Patricio): the
service also speaks English, for Erasmus students and newcomers, who are hit hardest.

Written for someone new to Spain: Spanish names they will meet (Catastro, nota simple, fianza)
stay, with a word on what they are. tests/test_i18n.py checks that every code in rubric.py has
its English here, so a new signal cannot reach an English report in Spanish.
"""

from __future__ import annotations

# code → (message, advice or None). Same advice presence as rubric.POINTS.
POINTS_EN: dict[str, tuple[str, str | None]] = {
    "pay_before_visit": (
        "They ask for money before you have seen the property in person.",
        "Do not pay anything, not even a booking fee or deposit, before visiting the property "
        "and signing a contract.",
    ),
    "owner_away": (
        "The advertiser says they are away and cannot show it.",
        "Ask for someone to show it to you in person, or an agency with an office you can visit.",
    ),
    "no_visit": (
        "They avoid an in-person visit (video call or photos only, or a visit after paying).",
        "Insist on an in-person visit before paying anything.",
    ),
    "keys_by_post": (
        "They offer to send you the keys by courier or through a third party after you pay.",
        "Keys are handed over in person when you sign; nobody serious posts them in advance.",
    ),
    "platform_payment": (
        "They say a platform (Airbnb, Idealista, a «secure payment» service…) handles or holds "
        "the payment.",
        "Long-term rental portals do not hold deposits. Check on the portal's official website, "
        "not through a link from the chat, that such a service exists.",
    ),
    "untraceable_payment": (
        "They ask for a payment that is hard to trace, or to a third party's name.",
        "Never use money transfers, cryptocurrency or gift cards for a rental.",
    ),
    "leave_platform": (
        "They ask you to leave the portal and continue by email or WhatsApp.",
        "Keep the conversation on the portal as long as you can: there is a record and the "
        "portal can act.",
    ),
    "urgency": (
        "They rush you: lots of interest, you must decide or pay now.",
        "Take your time: pressure is a scam's main tool.",
    ),
    "bargain_price": (
        "The price is very low for what is offered.",
        "Compare it with similar listings in the area.",
    ),
    "id_unprompted": (
        "They send you their ID document without you asking.",
        "An ID sent over chat proves nothing: it may be stolen.",
    ),
    "docs_before_visit": (
        "They ask for your documents (ID, payslips, contract) before showing you the property.",
        "Do not send documents before visiting. If you must, cover what is not needed and add a "
        "visible watermark («Only for the rental of …, date»).",
    ),
    "sob_story": ("They tell an elaborate personal story to explain the situation.", None),
    "tourist_as_long_term": (
        "It looks like a holiday let offered as a long-term rental.",
        "Check the tourist licence number in the Comunidad de Madrid register.",
    ),
    "inconsistency": ("Some details do not match each other.", None),
    "ai_instructions": (
        "The text includes instructions aimed at automated systems so they rate it as safe.",
        "A genuine advertiser has no reason to do this.",
    ),
    "evasive_on_checks": (
        "When you ask for a reasonable check, they refuse or make excuses.",
        "This is the clearest sign: a real landlord has no problem showing the property and "
        "signing before taking any money.",
    ),
    "in_person_visit": (
        "They offer an in-person visit before any payment (or you have already visited).",
        None,
    ),
    "contract_before_payment": (
        "Payment happens when a contract or document is signed in person.",
        None,
    ),
    "identified_agency": (
        "It is an identified agency or gestoría (administrative agency) with an office or a "
        "specific address.",
        None,
    ),
    "identified_company": (
        "The advertiser presents itself as a company or platform with its own website.",
        "Make sure it really is that company: go to its official website yourself (not through "
        "the link in the chat) and confirm the phone number and the listing are theirs.",
    ),
    "gives_verifiable_data": (
        "They give you details you can check (exact address, cadastral reference, company tax "
        "number).",
        "Check them: the cadastral reference on the Catastro website, and the owner with a "
        "«nota simple» from the Land Registry (you can order it online).",
    ),
    "official_deposit": (
        "They mention lodging the deposit with the official body.",
        None,
    ),
    "lookalike_domain": (
        "There is a link to a domain imitating a well-known portal or platform.",
        "Never pay through a link sent to you in a chat: go to the official website yourself.",
    ),
    "iban_foreign": (
        "The account to pay into is in another country.",
        "Be wary if you are asked to pay into a foreign account before signing in person.",
    ),
    "phone_foreign": (
        "The advertiser's contact phone is from another country.",
        "A foreign phone is not bad in itself, but do not pay anything before visiting the "
        "property in person.",
    ),
    "iban_invalid": (
        "The IBAN is not valid (it may be a copying mistake).",
        "Check the IBAN digit by digit before any transfer.",
    ),
    "iban_multiple": ("Several different bank accounts appear.", None),
    "listing_removed": (
        "This listing no longer appears on the portal, but it is still being offered to you.",
        "Open the link yourself: if idealista says it is no longer published and you are still "
        "being offered it elsewhere, that is the usual way a real flat gets recycled. If it says "
        "«Anuncio sospechoso» (suspicious listing), the portal itself removed it. Do not pay "
        "anything without seeing it with whoever has the keys.",
    ),
    "price_mismatch": (
        "The price you are given is much lower than the one published on the portal.",
        "Ask why the price does not match the published listing.",
    ),
    "seen_before_red": (
        "These details already appeared in other people's analyses that came out red.",
        "Do not pay anything and check everything in person: this advertiser has already "
        "appeared in cases with scam patterns.",
    ),
    "reused_elsewhere": (
        "The same property (address, listing or photos) appears in another analysis with a "
        "different advertiser.",
        "That is typical of a listing copied from a real one: do not pay anything without seeing "
        "the property with whoever really has the keys.",
    ),
    "street_not_found": (
        "We could not find that street in that town in the OpenStreetMap street map.",
        "It may be a typo. Ask for the exact address and look it up on the Catastro website: if "
        "it does not exist, do not go ahead.",
    ),
    "fee_to_tenant": (
        "They ask you to pay management fees, agency fees or a commission.",
        "Since the 2023 housing law (Ley de Vivienda), in a home rental the agency and contract "
        "fees are the landlord's, not the tenant's. Ask why they charge you, and do not pay "
        "anything before signing: charging «fees» up front is also a scammer's trick.",
    ),
    "iban_es": ("The account is at a Spanish bank.", None),
}

# Phrases to send the advertiser (rubric.ASKS), same rules: eager and cooperative, true
# pretexts, never an accusation. An English speaker writes to the advertiser in English.
ASKS_EN: dict[str, tuple[str, str]] = {
    "pay_before_visit": (
        "It's perfect for me! Can we meet this Saturday to see it, and I'll give you the deposit "
        "in person that same day?",
        "If it is a scam, they will not be able to meet: they will make excuses and ask for the "
        "money first again.",
    ),
    "no_visit": (
        "Great! Could I drop by tomorrow afternoon for a quick look? Whenever suits you.",
        "Whoever has the flat gives you a day and a time; a scammer looks for reasons not to meet.",
    ),
    "owner_away": (
        "No rush, when are you back? I'll wait to see it with you, or with whoever has the keys.",
        "If it is a scam, nobody will have keys and they will push you to pay first.",
    ),
    "keys_by_post": (
        "Perfect. And will you or someone on your behalf give me the keys the day we sign?",
        "If they insist on sending them by courier after you pay, that is the scam pattern.",
    ),
    "platform_payment": (
        "What is that service called inside the app? I'd rather do it from my own account, I "
        "have it installed.",
        "If they only accept a link they send you, it is fake: those payments do not exist "
        "outside the official app.",
    ),
    "untraceable_payment": (
        "I don't use Western Union. Can I pay you by bank transfer to your account when we sign?",
        "If they will not accept a transfer in their name after signing, they are not a landlord.",
    ),
    "iban_foreign": (
        "Is the account in your name? It's to put it in the contract.",
        "If the holder is not the owner, or they dodge the question, that is a bad sign.",
    ),
    "evasive_on_checks": (
        "OK, I understand. What if I come and see it with you any day this week?",
        "By now, if they still will not meet, you have your answer.",
    ),
    "leave_platform": (
        "I'd rather keep writing here, so I have everything in one place.",
        "If they push hard to take you off the portal, be wary.",
    ),
    "urgency": (
        "I'm really interested! Can I come and see it today or tomorrow, whenever suits you?",
        "If there is so much hurry but they will not let you see it, there is no flat to show.",
    ),
    "docs_before_visit": (
        "Sure, no problem. Is it OK if I bring my payslips the day I see it?",
        "If they demand documents before showing you anything, careful: they may be used to "
        "impersonate you.",
    ),
    "bargain_price": (
        "To get the contract ready, could you tell me the full address, with floor and door?",
        "With the address, check it yourself on the Catastro and order the «nota simple» from "
        "the Land Registry (see the advice). If they will not give it, bad sign.",
    ),
    "tourist_as_long_term": (
        "Will the contract be a regular housing lease («vivienda habitual») of at least a year, "
        "with the deposit lodged at the Agencia de Vivienda Social?",
        "If they cannot give you a regular housing lease, it is not a long-term rental.",
    ),
    "identified_company": (
        "Could you send me the company tax number (CIF) and the office address? It's for the "
        "contract.",
        "A real company gives them without any trouble; check them yourself on its official "
        "website.",
    ),
    "reused_elsewhere": (
        "I love it! Can I come and see it tomorrow afternoon? I'd like to see how much light it "
        "gets.",
        "If the photos or the address belong to another listing, they will not be able to show "
        "it to you.",
    ),
    "seen_before_red": (
        "Great! Shall we meet to see it and, if it suits me, sign and pay that same day?",
        "If they make excuses not to meet again, do not go ahead.",
    ),
    "street_not_found": (
        "To get the contract ready, could you confirm the full address, with floor and door?",
        "Look it up on the Catastro: if it does not exist or they give you a different one, do "
        "not go ahead.",
    ),
    "listing_removed": (
        "I'm interested! Can we meet to see it this week, and sign there if it suits me?",
        "If the listing no longer exists and they still cannot show it to you, it is a recycled "
        "flat.",
    ),
    "fee_to_tenant": (
        "Great. Will you give me an invoice for the fee with the agency's name and tax number "
        "(CIF)? I need it for my paperwork.",
        "A real agency invoices with its CIF and does not charge before signing; if there is no "
        "company and no invoice, do not pay.",
    ),
    "price_mismatch": (
        "I saw a different price in the listing, which one is valid for the contract?",
        "If the price drops «just for you» in exchange for paying first, be wary.",
    ),
    "phone_foreign": (
        "Who would show me the flat, and which day could it be?",
        "If nobody can show it to you in person, do not pay anything.",
    ),
}
DEFAULT_ASK_EN = (
    "To get the contract ready, could you tell me the full address, with floor and door, and "
    "who would sign as the owner?",
    "With the address and the name, order the «nota simple» from the Land Registry and check "
    "that they match.",
)
