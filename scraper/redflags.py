"""Rule-based scam / risk warnings shown to the user next to a job."""

from __future__ import annotations

import re

from .extract import NO_EXPERIENCE_RE, extract_emails, extract_phones, is_free_email

FLAG_FEE = "Asks for a payment (registration, processing or other fee)"
FLAG_WHATSAPP = "Contact is only through WhatsApp"
FLAG_PHONE = "Contact is only through a personal phone number"
FLAG_FREE_EMAIL = "Uses a free email address (e.g. Gmail/Yahoo) and no company is named"
FLAG_HIGH_PAY = "Very high pay offered for no experience"
FLAG_NO_COMPANY = "Company name is not stated"
FLAG_OVERSEAS = "Overseas job or visa offer – verify carefully before paying or travelling"

HIGH_PAY_MONTHLY_NAIRA = 500_000

FEE_RE = re.compile(
    r"(registration|processing|application|form|training|medical|screening|clearance|"
    r"documentation|admin(?:istrative)?|interview|uniform|logistics|verification)\s+(fee|charge|levy)s?"
    r"|\bpay\s+(?:a|the|an)?\s*(?:token|fee|sum)"
    r"|\b(?:pay|send|transfer)\s+(?:₦|N|NGN|#)\s?\d"
    r"|\bnon[- ]refundable\b"
    r"|\bfee\s+of\s+(?:₦|N|NGN|#)\s?\d",
    re.I,
)
# "No fee required" / "we never charge" must not trigger the fee flag
NO_FEE_RE = re.compile(
    r"(no|zero|free of|without|never|do(?:es)? not|don'?t|will not|won'?t)\s+(?:[\w,]+\s+){0,5}?(fees?|charges?|pay(?:ment)?)",
    re.I,
)
WHATSAPP_RE = re.compile(r"\bwhats\s?app\b|\bwa\.me/", re.I)
OVERSEAS_RE = re.compile(
    r"\b(visa sponsorship|visa (?:processing|assistance|support)|work permit|work visa|"
    r"travel abroad|work abroad|jobs? abroad|overseas|relocat\w+ to (?:canada|the uk|uk|usa|europe|dubai|qatar|saudi|australia)|"
    r"(?:jobs?|work|employment) in (?:canada|the uk|uk|usa|dubai|uae|qatar|saudi arabia|kuwait|oman|poland|malta|europe|australia))\b",
    re.I,
)


def detect_red_flags(
    *,
    text: str,
    company: str | None,
    salary_monthly: float | None,
    has_official_link: bool,
    trusted_source: bool = False,
) -> list[str]:
    """`text` is the original ad text. `has_official_link` = an apply URL / website is given.

    `trusted_source` is for curated job boards (RSS feeds) where contact details are handled
    by the board itself, so we skip the contact-method checks.
    """
    flags: list[str] = []
    text = text or ""

    for fee_hit in FEE_RE.finditer(text):
        # Same line, up to 80 chars before: "We never charge registration fees" is reassurance, not a red flag
        line_start = text.rfind("\n", 0, fee_hit.start()) + 1
        around = text[max(line_start, fee_hit.start() - 80): fee_hit.end() + 10]
        if not NO_FEE_RE.search(around):
            flags.append(FLAG_FEE)
            break

    emails = extract_emails(text)
    phones = extract_phones(text)
    company_known = bool(company)

    if not trusted_source:
        if not emails and not has_official_link:
            if WHATSAPP_RE.search(text):
                flags.append(FLAG_WHATSAPP)
            elif phones:
                flags.append(FLAG_PHONE)
        if emails and all(is_free_email(e) for e in emails) and not company_known:
            flags.append(FLAG_FREE_EMAIL)

    if salary_monthly and salary_monthly >= HIGH_PAY_MONTHLY_NAIRA and NO_EXPERIENCE_RE.search(text):
        flags.append(FLAG_HIGH_PAY)

    if not company_known:
        flags.append(FLAG_NO_COMPANY)

    if OVERSEAS_RE.search(text):
        flags.append(FLAG_OVERSEAS)

    return flags
