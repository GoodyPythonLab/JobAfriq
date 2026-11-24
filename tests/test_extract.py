from datetime import date

import pytest

from scraper import extract as ex

TODAY = date(2026, 11, 24)


def test_emails_and_phones():
    text = "Send CV to HR@Acme.com.ng or hr@acme.com.ng. Call 0803 123 4567, +234 805-555-1234 or 2348091112222."
    assert ex.extract_emails(text) == ["hr@acme.com.ng"]
    assert ex.extract_phones(text) == ["08031234567", "08055551234", "08091112222"]
    assert ex.extract_phones("ref no 12345678901") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Deadline: 30th November, 2026.", "2026-11-30"),
        ("Application closes on December 5, 2026", "2026-12-05"),
        ("Apply on or before 2026-12-10", "2026-12-10"),
        ("Closing date: 15/12/2026", "2026-12-15"),
        ("Deadline: 3rd Dec", "2026-12-03"),
        ("No deadline mentioned", None),
    ],
)
def test_deadlines(text, expected):
    assert ex.extract_deadline(text, TODAY) == expected


@pytest.mark.parametrize(
    "text,label,monthly",
    [
        ("Salary: ₦250,000 - ₦300,000 per month", "₦250,000 - ₦300,000 per month", 300_000),
        ("Salary: N750,000 monthly.", "₦750,000 per month", 750_000),
        ("Remuneration: NGN 3.6m per annum", "₦3,600,000 per year", 300_000),
        ("Pay: N150k", "₦150,000", 150_000),
    ],
)
def test_salary(text, label, monthly):
    got_label, got_monthly = ex.extract_salary(text)
    assert got_label == label
    assert got_monthly == pytest.approx(monthly)


def test_salary_ignores_company_size():
    assert ex.extract_salary("A portfolio size of over N15 billion in assets.") == (None, None)
    assert ex.extract_salary("Registration fee is N5,000") == (None, None)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Location: Ikeja, Lagos", "Ikeja, Lagos"),
        ("The position is located in Lagos State.", "Lagos"),
        ("Offices in Abuja and Port Harcourt", "Abuja, Rivers"),
        ("Experienced Driver Wanted In Port Harcourt", "Port Harcourt, Rivers"),
        ("This is a remote role", "Remote"),
        ("We operate across the Niger Delta region", "Nigeria"),
        ("Many rivers to cross", "Nigeria"),
        ("Edo North, Central and Ovia - Edo State", "Edo"),
    ],
)
def test_location(text, expected):
    assert ex.detect_location(text) == expected


@pytest.mark.parametrize(
    "title,text,expected",
    [
        ("Accountant", "ICAN qualified, audit experience", "Accounting"),
        ("Senior Backend Developer", "Python, Django, cloud", "IT/Tech"),
        ("Mechanical Engineer", "maintenance of plant", "Engineering"),
        ("Sales Executive", "meet sales targets", "Sales/Marketing"),
        ("Customer Care Representative", "handle calls in our call center", "Customer Service"),
        ("Receptionist", "front office duties", "Admin/Office"),
        ("Registered Nurse", "hospital ward", "Healthcare"),
        ("Mathematics Teacher", "secondary school", "Education"),
        ("Dispatch Rider", "deliver parcels", "Driving/Logistics"),
        ("Federal Civil Service Recruitment 2026", "ministry vacancies on the recruitment portal", "Government"),
        ("Programme Officer", "humanitarian NGO supporting IDPs", "NGO"),
        ("Graduate Trainee Programme 2026", "for fresh graduates in accounting", "Internship/Graduate"),
        ("Assistant", "general duties", "Other"),
    ],
)
def test_categorize(title, text, expected):
    assert ex.categorize(title, text) == expected


def test_category_hint_from_feed():
    assert ex.categorize("Officer", "", hint="Banking / Finance") == "Accounting"


def test_job_type_and_experience():
    assert ex.detect_job_type("Accountant", "Job Type: Full-time") == "Full-time"
    assert ex.detect_job_type("Sales Intern", "") == "Internship"
    assert ex.detect_job_type("Writer", "This is a part-time role") == "Part-time"
    assert ex.detect_experience("Minimum of 3 years experience") == "3+ years"
    assert ex.detect_experience("2 - 5 years of relevant work experience") == "2-5 years"
    assert ex.detect_experience("No experience needed") == "Entry level"
    assert ex.detect_experience("Good attitude") == "Not specified"


def test_company_extraction():
    assert ex.extract_company("Field Officer at Moniepoint Incorporated", "") == "Moniepoint Incorporated"
    assert ex.extract_company("Accountant Needed", "Greenfield Foods Limited is recruiting for") == "Greenfield Foods Limited"
    assert ex.extract_company("Urgent hiring", "We are hiring customer care reps") is None


def test_mojibake_and_titles():
    assert ex.fix_mojibake("weâ\x80\x99re") == "we’re"
    assert ex.fix_mojibake("weâ€™re ₦5,000") == "we’re ₦5,000"
    assert ex.tidy_title("Re: URGENT VACANCY FOR AN ACCOUNTANT IN LAGOS") == "Urgent Vacancy for an Accountant in Lagos"
    assert ex.tidy_title("HR OFFICER NEEDED IN ABUJA") == "HR Officer Needed in Abuja"


def test_title_case_after_punctuation_and_company_parenthesis():
    assert ex.tidy_title("STOREKEEPER -PORT HARCOURT") == "Storekeeper -Port Harcourt"
    assert ex.extract_company("Job Vacancies At Mobojhub (Lagos – Ejigbo/Isolo)", "") == "Mobojhub"
