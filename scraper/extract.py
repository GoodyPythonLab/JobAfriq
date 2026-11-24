"""Regex/keyword based field extraction from job-ad text."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

WAT = timezone(timedelta(hours=1))  # Nigeria (no daylight saving)

# --------------------------------------------------------------------------- text helpers

_WS = re.compile(r"[ \t ]+")

# Characters that cp1252 puts in the 0x80-0x9F range (seen in double-encoded UTF-8).
_CP1252_HIGH = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
_MOJIBAKE_RE = re.compile("[Â-ô][\u0080-¿" + _CP1252_HIGH + "]{1,3}")


def _unmangle(m: re.Match) -> str:
    s = m.group(0)
    raw = bytearray()
    for ch in s:
        try:
            raw += ch.encode("cp1252")
        except UnicodeEncodeError:
            try:
                raw += ch.encode("latin-1")
            except UnicodeEncodeError:
                return s
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return s


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 that was decoded as Latin-1/cp1252 ("weâ€™re" -> "we’re")."""
    return _MOJIBAKE_RE.sub(_unmangle, text) if text else text


def clean_text(text: str) -> str:
    text = fix_mojibake(text or "").replace("\r", "")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()


ACRONYMS = {
    "HR", "IT", "ICT", "NGO", "CEO", "CFO", "CTO", "COO", "MD", "GM", "HSE", "QHSE", "QA", "UI", "UX",
    "NYSC", "OND", "HND", "BSC", "MSC", "FCT", "SME", "ICAN", "ACCA", "SIWES", "POS", "PLC", "LTD",
    "NIG", "UK", "USA", "UAE", "M&E", "SEO", "CV", "WAEC", "SSCE", "ATS", "AI", "PR", "NNPC",
}
SMALL_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to", "with", "by"}


def tidy_title(title: str) -> str:
    title = clean_text(title).replace("\n", " ")
    title = re.sub(r"^(re:\s*)+", "", title, flags=re.I)
    title = title.strip(" -|:")
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(letters) > 6:
        # SHOUTY TITLE -> Title Case, keeping known acronyms
        first = [True]

        def fix(m: re.Match) -> str:
            w = m.group(0)
            is_first, first[0] = first[0], False
            if w.upper() in ACRONYMS:
                return w.upper()
            if not is_first and w.lower() in SMALL_WORDS:
                return w.lower()
            return w.capitalize()

        title = re.sub(r"[A-Za-z]+", fix, title)
    return title[:160]


def normalise_key(*parts: str | None) -> str:
    joined = " ".join((p or "") for p in parts).lower()
    joined = re.sub(r"\b(ltd|limited|plc|nig|nigeria|inc|llc|company|co)\b", " ", joined)
    return re.sub(r"[^a-z0-9]+", " ", joined).strip()


# --------------------------------------------------------------------------- contacts

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?234[\s.-]?\(?0?\)?|0)[789][01](?:[\s.-]?\d){8}(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "rocketmail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mail.com", "gmx.com", "protonmail.com", "proton.me", "yandex.com", "zoho.com",
}


def extract_emails(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in EMAIL_RE.findall(text or ""):
        seen.setdefault(m.lower().rstrip("."), None)
    return list(seen)


def extract_phones(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", m)
        if digits.startswith("2340"):
            digits = "234" + digits[4:]
        if digits.startswith("234"):
            digits = "0" + digits[3:]
        if len(digits) == 11:
            seen.setdefault(digits, None)
    return list(seen)


def extract_urls(text: str, exclude_hosts: tuple[str, ...] = ()) -> list[str]:
    out: dict[str, None] = {}
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:")
        if any(h in u.lower() for h in exclude_hosts):
            continue
        out.setdefault(u, None)
    return list(out)


def is_free_email(email: str) -> bool:
    return email.split("@")[-1].lower() in FREE_EMAIL_DOMAINS


# --------------------------------------------------------------------------- dates

MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DATE_FORMS = [
    # 30th November, 2026 / 30 Nov 2026 / 30th of November
    re.compile(_DAY + r"(?:\s+of)?[\s,.-]+" + _MON + r"\.?(?:[\s,.-]+(\d{4}))?", re.I),
    # November 30th, 2026
    re.compile(_MON + r"\.?\s+" + _DAY + r"(?:[\s,]+(\d{4}))?", re.I),
    # 2026-11-30
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    # 30/11/2026 or 30-11-2026 (Nigeria uses day first)
    re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})"),
]

DEADLINE_CUE = re.compile(
    r"(deadline|closing date|closes?\s+on|application(?:s)?\s+(?:closes?|ends?)|"
    r"on or before|not later than|no later than|latest by|last date|submit(?:ted)?\s+by|before)",
    re.I,
)


def _mk_date(y: int | None, m: int, d: int, today: date) -> date | None:
    try:
        if y is None:
            guess = date(today.year, m, d)
            # "Nov 30" seen in January most likely meant last year; ads rarely look >6 months ahead
            if (guess - today).days > 183:
                guess = date(today.year - 1, m, d)
            elif (today - guess).days > 183:
                guess = date(today.year + 1, m, d)
            return guess
        if y < 100:
            y += 2000
        return date(y, m, d)
    except ValueError:
        return None


def parse_loose_date(text: str, today: date) -> date | None:
    for i, rx in enumerate(_DATE_FORMS):
        m = rx.search(text)
        if not m:
            continue
        g = m.groups()
        if i == 0:
            d = _mk_date(int(g[2]) if g[2] else None, MONTHS[g[1].lower()[:3]], int(g[0]), today)
        elif i == 1:
            d = _mk_date(int(g[2]) if g[2] else None, MONTHS[g[0].lower()[:3]], int(g[1]), today)
        elif i == 2:
            d = _mk_date(int(g[0]), int(g[1]), int(g[2]), today)
        else:
            d = _mk_date(int(g[2]), int(g[1]), int(g[0]), today)
        if d:
            return d
    return None


def extract_deadline(text: str, today: date | None = None) -> str | None:
    today = today or datetime.now(WAT).date()
    for m in DEADLINE_CUE.finditer(text or ""):
        window = text[m.end(): m.end() + 60]
        d = parse_loose_date(window, today)
        if d and abs((d - today).days) < 400:
            return d.isoformat()
    return None


# --------------------------------------------------------------------------- salary

_AMOUNT = r"(?:₦|N|NGN|#)\s?(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s?(k|m|million|thousand)?\b"
SALARY_AMOUNT_RE = re.compile(_AMOUNT, re.I)
SALARY_RANGE_RE = re.compile(_AMOUNT + r"(?:\s*(?:-|–|to)\s*" + _AMOUNT.replace("(?:₦|N|NGN|#)\\s?", "(?:₦|N|NGN|#)?\\s?") + r")?", re.I)
SALARY_CUE = re.compile(r"\b(salary|pay|remuneration|stipend|compensation|earn|wage)s?\b", re.I)
PERIOD_RE = re.compile(r"(per\s+month|monthly|/\s?month|\bpm\b|per\s+annum|annually|/\s?year|per\s+year|p\.?a\.?\b|weekly|per\s+week|daily|per\s+day)", re.I)


def _to_number(num: str, unit: str | None) -> float:
    num = num.replace(",", "")
    if num.count(".") > 1 or (num.count(".") == 1 and len(num.split(".")[1]) == 3 and not unit):
        num = num.replace(".", "")  # 150.000 written European-style
    value = float(num)
    unit = (unit or "").lower()
    if unit in ("k", "thousand"):
        value *= 1_000
    elif unit in ("m", "million"):
        value *= 1_000_000
    return value


def _fmt_naira(v: float) -> str:
    return "₦" + f"{v:,.0f}"


def extract_salary(text: str) -> tuple[str | None, float | None]:
    """Return (human-readable salary, monthly upper value in naira) or (None, None)."""
    for line in (text or "").split("\n"):
        has_cue = bool(SALARY_CUE.search(line))
        for m in SALARY_RANGE_RE.finditer(line):
            tail = line[m.end(): m.end() + 25]
            period_m = PERIOD_RE.search(tail) or (PERIOD_RE.search(line) if has_cue else None)
            if not (has_cue or period_m):
                continue
            g = m.groups()
            low = _to_number(g[0], g[1])
            high = _to_number(g[2], g[3] or g[1]) if g[2] else None
            if low < 5_000 and not g[1]:
                continue  # "N5" etc. is noise, not a salary
            if (high or low) > 100_000_000:
                continue  # company size, not pay
            label = _fmt_naira(low) + (f" - {_fmt_naira(high)}" if high else "")
            monthly = high or low
            if period_m:
                p = period_m.group(1).lower()
                if any(k in p for k in ("annum", "annual", "year", "p.a", "pa")):
                    label += " per year"
                    monthly /= 12
                elif "week" in p:
                    label += " per week"
                    monthly *= 4.3
                elif "day" in p or "daily" in p:
                    label += " per day"
                    monthly *= 22
                else:
                    label += " per month"
            return label, monthly
    return None, None


# --------------------------------------------------------------------------- location

STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Gombe", "Imo", "Jigawa",
    "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger",
    "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", "Yobe", "Zamfara",
]
CITIES = {
    # Abuja / FCT
    "Abuja": "Abuja", "FCT": "Abuja", "Garki": "Abuja", "Wuse": "Abuja", "Maitama": "Abuja",
    "Gwarinpa": "Abuja", "Asokoro": "Abuja", "Kubwa": "Abuja", "Jabi": "Abuja", "Utako": "Abuja",
    "Lugbe": "Abuja", "Gwagwalada": "Abuja",
    # Lagos
    "Ikeja": "Lagos", "Lekki": "Lagos", "Victoria Island": "Lagos", "Ikoyi": "Lagos",
    "Yaba": "Lagos", "Surulere": "Lagos", "Ajah": "Lagos", "Apapa": "Lagos", "Festac": "Lagos",
    "Ikorodu": "Lagos", "Oshodi": "Lagos", "Gbagada": "Lagos", "Magodo": "Lagos", "Ogba": "Lagos",
    "Agege": "Lagos", "Badagry": "Lagos", "Epe": "Lagos", "Ojota": "Lagos", "Ketu": "Lagos",
    "Isolo": "Lagos", "Ilupeju": "Lagos", "Ojo": "Lagos", "Alimosho": "Lagos", "Maryland": "Lagos",
    "Ikotun": "Lagos", "Egbeda": "Lagos", "Mushin": "Lagos", "Iyana Ipaja": "Lagos", "Sangotedo": "Lagos",
    "Ojodu": "Lagos", "Berger": "Lagos", "Ilasamaja": "Lagos", "Idimu": "Lagos", "Ejigbo": "Lagos",
    "Agbara": "Ogun", "Ibafo": "Ogun", "Arepo": "Ogun", "Ifo": "Ogun", "Ijoko": "Ogun", "Ilaro": "Ogun",
    # Others
    "Port Harcourt": "Rivers", "Ibadan": "Oyo", "Ogbomoso": "Oyo", "Benin City": "Edo",
    "Abeokuta": "Ogun", "Ota": "Ogun", "Sango Ota": "Ogun", "Mowe": "Ogun", "Sagamu": "Ogun",
    "Ijebu Ode": "Ogun", "Ijebu-Ode": "Ogun", "Warri": "Delta", "Asaba": "Delta", "Sapele": "Delta",
    "Uyo": "Akwa Ibom", "Eket": "Akwa Ibom", "Calabar": "Cross River", "Owerri": "Imo",
    "Onitsha": "Anambra", "Awka": "Anambra", "Nnewi": "Anambra", "Aba": "Abia", "Umuahia": "Abia",
    "Ilorin": "Kwara", "Jos": "Plateau", "Zaria": "Kaduna", "Akure": "Ondo", "Osogbo": "Osun",
    "Ile-Ife": "Osun", "Ado-Ekiti": "Ekiti", "Ado Ekiti": "Ekiti", "Lokoja": "Kogi",
    "Makurdi": "Benue", "Minna": "Niger", "Yola": "Adamawa", "Maiduguri": "Borno",
    "Lafia": "Nasarawa", "Yenagoa": "Bayelsa", "Abakaliki": "Ebonyi", "Nsukka": "Enugu",
    "Dutse": "Jigawa", "Damaturu": "Yobe", "Jalingo": "Taraba", "Gusau": "Zamfara",
    "Birnin Kebbi": "Kebbi", "Bonny": "Rivers",
}
_STATE_NAMES = {s: s for s in STATES}
_STATE_NAMES["FCT"] = "Abuja"
_LOC_TERMS = sorted({**_STATE_NAMES, **CITIES}.items(), key=lambda kv: -len(kv[0]))
# Case-sensitive (Title Case or UPPER CASE) so words like "rivers"/"delta" in prose don't match.
_LOC_RE = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(re.escape(t) + "|" + re.escape(t.upper()) for t, _ in _LOC_TERMS)
    + r")(?![A-Za-z])"
)
_LOOKUP = {}
for term, state in _LOC_TERMS:
    _LOOKUP[term] = (term, state)
    _LOOKUP[term.upper()] = (term, state)
REMOTE_RE = re.compile(r"\b(remote(ly)?|work from home|wfh|hybrid)\b", re.I)


def detect_location(*texts: str) -> str:
    """Return e.g. "Ikeja, Lagos", "Lagos", "Lagos, Abuja", "Remote" or "Nigeria"."""
    for text in texts:
        if not text:
            continue
        states: list[str] = []
        city_for_state: dict[str, str] = {}
        for m in _LOC_RE.finditer(text):
            term, state = _LOOKUP[m.group(1)]
            # "Niger Delta", "Delta Air" etc.
            following = text[m.end(): m.end() + 8].lower()
            preceding = text[max(0, m.start() - 6): m.start()].lower()
            if term == "Niger" and "delta" in following:
                continue
            if term == "Delta" and ("niger" in preceding or "air" in following):
                continue
            if state not in states:
                states.append(state)
            if term not in (state, "FCT") and state != "Abuja":
                city_for_state.setdefault(state, term)
        if states:
            if len(states) == 1:
                city = city_for_state.get(states[0])
                return f"{city}, {states[0]}" if city else states[0]
            return ", ".join(states[:3])
        if REMOTE_RE.search(text):
            return "Remote"
    return "Nigeria"


# --------------------------------------------------------------------------- category

CATEGORIES: dict[str, list[str]] = {
    "Internship/Graduate": [
        "intern", "internship", "graduate trainee", "graduate programme", "graduate program",
        "trainee", "nysc", "corps member", "siwes", "industrial training", "apprentice",
        "fresh graduate", "entry level programme",
    ],
    "Accounting": [
        "accountant", "accounting", "accounts", "account officer", "audit", "auditor",
        "bookkeeper", "bookkeeping", "finance", "financial", "tax", "treasury", "payroll",
        "cashier", "ican", "acca", "credit officer", "loan officer", "banking", "debt recovery",
        "recovery officer", "collections officer", "investment",
    ],
    "Engineering": [
        "engineer", "engineering", "mechanical", "electrical", "civil", "technician",
        "maintenance engineer", "maintenance technician", "construction", "site engineer", "site supervisor", "welder", "welding", "plumber", "electrician",
        "hse", "qhse", "oil and gas", "production engineer", "production supervisor", "quantity surveyor",
        "architect", "surveyor", "mechanic", "artisan", "solar",
    ],
    "IT/Tech": [
        "developer", "software", "programmer", "frontend", "front-end", "backend", "back-end",
        "full stack", "fullstack", "devops", "it support", "it officer", "ict", "network",
        "cybersecurity", "data analyst", "data scientist", "data engineer", "web", "mobile app",
        "flutter", "python", "javascript", "react", "product manager", "ui/ux", "ux", "cloud",
        "database", "system administrator", "computer", "tech", "graphic designer",
    ],
    "Sales/Marketing": [
        "sales", "marketing", "marketer", "business development", "brand", "digital marketing",
        "social media", "content", "seo", "merchandiser", "promoter", "field agent",
        "relationship manager", "growth", "advert", "communications", "public relations",
        "real estate", "realtor", "property consultant",
    ],
    "Customer Service": [
        "customer service", "customer care", "customer support", "customer experience",
        "call center", "call centre", "contact center", "contact centre", "help desk",
        "helpdesk", "client service", "front desk", "telemarketer", "telesales",
    ],
    "Admin/Office": [
        "admin", "administrative", "administrator", "secretary", "receptionist",
        "office assistant", "personal assistant", "executive assistant", "clerk", "office manager",
        "human resource", "human resources", "hr ", "hr officer", "hr manager", "recruiter",
        "data entry", "store keeper", "storekeeper", "procurement", "operations officer",
    ],
    "Healthcare": [
        "nurse", "nursing", "doctor", "medical", "physician", "pharmacist", "pharmacy",
        "hospital", "clinic", "clinical", "laboratory", "lab scientist", "health", "midwife",
        "physiotherapist", "dentist", "dental", "radiographer", "optometrist", "caregiver",
        "community health",
    ],
    "Education": [
        "teacher", "teaching", "lecturer", "tutor", "school", "academic", "education",
        "instructor", "headteacher", "head teacher", "principal", "nanny", "creche", "montessori",
        "university", "college",
    ],
    "Driving/Logistics": [
        "driver", "driving", "dispatch rider", "rider", "logistics", "delivery", "warehouse",
        "supply chain", "fleet", "haulage", "courier", "transport", "shipping", "dispatch",
        "forklift", "inventory", "loader", "loaders",
    ],
    "Government": [
        "federal government", "state government", "ministry", "civil service", "recruitment portal",
        "federal civil service", "nigeria police", "nigerian army", "nigerian navy",
        "nigerian air force", "customs service", "immigration service", "nscdc", "efcc",
        "inec", "frsc", "local government", "public service", "parastatal",
    ],
    "NGO": [
        "ngo", "non-governmental", "non-profit", "nonprofit", "humanitarian", "unicef", "undp",
        "unhcr", "world health organization", "save the children", "foundation", "charity",
        "international rescue committee", "msf", "médecins", "development programme",
        "programme officer", "program officer", "monitoring and evaluation", "m&e",
    ],
}
_CAT_RES = {
    cat: [re.compile(r"(?<![a-z])" + re.escape(k.strip()) + r"(?:s|es)?(?![a-z])") for k in kws]
    for cat, kws in CATEGORIES.items()
}
# Hints from feed metadata such as MyJobMag's <industry> field
INDUSTRY_HINTS = {
    "accounting": "Accounting", "finance": "Accounting", "banking": "Accounting",
    "engineering": "Engineering", "oil": "Engineering", "construction": "Engineering",
    "ict": "IT/Tech", "information technology": "IT/Tech", "telecom": "IT/Tech", "software": "IT/Tech",
    "sales": "Sales/Marketing", "marketing": "Sales/Marketing", "advertising": "Sales/Marketing",
    "health": "Healthcare", "medical": "Healthcare", "hospital": "Healthcare", "pharma": "Healthcare",
    "education": "Education", "teaching": "Education", "school": "Education",
    "logistics": "Driving/Logistics", "transport": "Driving/Logistics",
    "government": "Government", "public sector": "Government",
    "ngo": "NGO", "non-profit": "NGO", "development": "NGO",
}


def categorize(title: str, text: str = "", hint: str | None = None) -> str:
    title_l = f" {(title or '').lower()} "
    text_l = f" {(text or '').lower()[:4000]} "
    scores: dict[str, float] = {}
    for cat, regs in _CAT_RES.items():
        s = 0.0
        for rx in regs:
            if rx.search(title_l):
                s += 4
            n = len(rx.findall(text_l))
            s += min(n, 3)
        if s:
            scores[cat] = s
    if hint:
        hl = hint.lower()
        for key, cat in INDUSTRY_HINTS.items():
            if key in hl:
                scores[cat] = scores.get(cat, 0) + 2
                break
    # Internship / graduate schemes win if the title says so
    if any(rx.search(title_l) for rx in _CAT_RES["Internship/Graduate"]):
        return "Internship/Graduate"
    if not scores:
        return "Other"
    best = max(scores.items(), key=lambda kv: (kv[1], -list(CATEGORIES).index(kv[0])))
    return best[0] if best[1] >= 2 else "Other"


# --------------------------------------------------------------------------- job type / experience

JOB_TYPES = [
    ("Internship", re.compile(r"\b(intern(ship)?|siwes|industrial training)\b", re.I)),
    ("Contract", re.compile(r"\b(contract|temporary|temp|fixed[- ]term|freelance|locum)\b", re.I)),
    ("Part-time", re.compile(r"\bpart[- ]?time\b", re.I)),
    ("Remote", re.compile(r"\b(remote|work from home)\b", re.I)),
    ("Full-time", re.compile(r"\bfull[- ]?time\b", re.I)),
]


def detect_job_type(title: str, text: str) -> str:
    for label, rx in JOB_TYPES:
        if rx.search(title or ""):
            return label
    for label, rx in JOB_TYPES:
        if rx.search(text or ""):
            return label
    return "Full-time"


NO_EXPERIENCE_RE = re.compile(
    r"\b(no (prior |previous )?experience( (is )?(required|needed))?|without experience|"
    r"fresh graduates?|entry[- ]level|experience not required|zero experience)\b",
    re.I,
)
_EXP_RE = re.compile(
    r"(?:minimum|min\.?|at least|over)?\s*(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?"
    r"(?:years?|yrs?)(?:'|’)?\s*(?:of\s+)?(?:\w+\s+){0,3}?(?:experience|post[- ]qualification|work)",
    re.I,
)


def detect_experience(text: str) -> str:
    m = _EXP_RE.search(text or "")
    if m:
        low, high = int(m.group(1)), m.group(2)
        if low > 40:
            return "Not specified"
        if high:
            return f"{low}-{int(high)} years"
        return f"{low}+ years" if low else "Entry level"
    if NO_EXPERIENCE_RE.search(text or ""):
        return "Entry level"
    return "Not specified"


# --------------------------------------------------------------------------- company

_SUFFIX = r"(?:Ltd|Limited|Plc|PLC|Nig\.?|Nigeria|Inc\.?|LLC|Group|Enterprises?|Ventures|Company|Hospital|Clinic|School|Academy|Foundation|Bank|Microfinance Bank|Industries|Services|Consult(?:ing|ants)?|Solutions|Partners|Agency|Hotels?)"
_COMPANY_PATTERNS = [
    re.compile(r"(?:company|organi[sz]ation|employer|company name|client)[ \t]*:[ \t]*([^\n]{3,80})", re.I),
    re.compile(r"(?:^|\n)[ \t]*(?:About[ \t]+)?([A-Z][\w&.,'()-]*(?:[ \t]+[A-Z&][\w&.,'()-]*){0,7})[ \t]+(?:is|are)[ \t]+(?:currently[ \t]+)?(?:recruiting|hiring|looking|seeking|in need)", re.M),
    re.compile(r"\b(?:at|with|for)[ \t]+([A-Z][\w&'.-]*(?:[ \t]+[A-Z&][\w&'.-]*){0,5}[ \t]+" + _SUFFIX + r")\b"),
    re.compile(r"\b([A-Z][\w&'.-]*(?:[ \t]+[A-Z&][\w&'.-]*){0,5}[ \t]+" + _SUFFIX + r")\b"),
]
_NOT_COMPANY = re.compile(
    r"^(we|our|a|an|the|my|reputable|leading|urgent|vacancy|vacancies|job|jobs|hiring|i|this|they|"
    r"why|about|join|who|what|how|role|position|requirements?|location|salary|apply|interested)\b",
    re.I,
)


def extract_company(title: str, text: str) -> str | None:
    m = re.search(r"\bat\s+(.+?)(?:\s*[-–|(]\s*\d+\s*openings?.*)?$", title or "", re.I)
    if m and not _NOT_COMPANY.match(m.group(1)):
        cand = m.group(1).strip(" .")
        # "Greenfield Foods Ltd, Ikeja" / "... in Lagos" -> drop the trailing location
        cut = re.match(r"^(.*?)(?:,\s*|\s+in\s+|\s*\()(.+)$", cand)
        if cut and _LOC_RE.search(cut.group(2)):
            cand = cut.group(1).strip(" .")
        if 2 < len(cand) <= 80 and cand[0].isupper():
            return cand
    for rx in _COMPANY_PATTERNS:
        for src in (title or "", text or ""):
            mm = rx.search(src)
            if mm:
                cand = mm.group(1).strip(" .,:-")
                if 2 < len(cand) <= 80 and not _NOT_COMPANY.match(cand) and not EMAIL_RE.search(cand):
                    return cand
    return None
