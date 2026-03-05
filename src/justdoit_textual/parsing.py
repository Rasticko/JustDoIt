from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

TIME_PATTERNS = (
    "%H:%M",
    "%H.%M",
    "%I:%M%p",
    "%I%p",
)

REPEAT_RULES = {
    "daily": "daily",
    "day": "daily",
    "every day": "daily",
    "weekly": "weekly",
    "week": "weekly",
    "every week": "weekly",
    "monthly": "monthly",
    "month": "monthly",
    "every month": "monthly",
    "yearly": "yearly",
    "year": "yearly",
    "annual": "yearly",
    "annually": "yearly",
    "every year": "yearly",
    "weekday": "weekdays",
    "weekdays": "weekdays",
    "every weekday": "weekdays",
    "every weekdays": "weekdays",
}


@dataclass
class CapturedTask:
    title: str
    deadline: date | None
    deadline_time: time | None
    recurrence_rule: str | None
    tags: list[str]
    attendees: list[str]


def normalize_repeat_rule(raw: str) -> str | None:
    return REPEAT_RULES.get(raw.strip().lower())


def _extract_inline_recurrence(raw: str) -> tuple[str, str | None]:
    matches: list[tuple[int, int, str]] = []
    for m in re.finditer(
        r"\bevery\s+(day|week|month|year|weekday|weekdays)\b",
        raw,
        flags=re.IGNORECASE,
    ):
        rule = normalize_repeat_rule(m.group(0))
        if rule:
            matches.append((m.start(), m.end(), rule))

    for m in re.finditer(
        r"\b(daily|weekly|monthly|yearly|annual|annually|weekday|weekdays)\b",
        raw,
        flags=re.IGNORECASE,
    ):
        # Prefer the full "every <rule>" phrase when present.
        if m.start() >= 6 and raw[m.start() - 6 : m.start()].lower() == "every ":
            continue
        rule = normalize_repeat_rule(m.group(0))
        if rule:
            matches.append((m.start(), m.end(), rule))

    if not matches:
        return raw, None

    matches.sort(key=lambda x: x[0])
    start, end, rule = matches[-1]
    stripped = (raw[:start] + " " + raw[end:]).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped, rule


def parse_time_token(raw: str) -> time | None:
    token = raw.strip()
    if not token:
        return None
    normalized = token.upper().replace(" ", "")
    for pattern in TIME_PATTERNS:
        try:
            return datetime.strptime(normalized, pattern).time()
        except ValueError:
            pass
    return None


def parse_natural_date(raw: str, *, today: date | None = None) -> date | None:
    s = raw.strip().lower()
    if not s:
        return None

    if today is None:
        today = date.today()

    if s == "today":
        return today
    if s == "tomorrow":
        return today + timedelta(days=1)
    if s == "yesterday":
        return today - timedelta(days=1)

    try:
        return date.fromisoformat(s)
    except ValueError:
        pass

    rel = re.fullmatch(r"in\s+(\d+)\s+(day|days|week|weeks)", s)
    if rel:
        n = int(rel.group(1))
        unit = rel.group(2)
        return today + timedelta(days=n * (7 if unit.startswith("week") else 1))

    if s.startswith("next "):
        weekday = WEEKDAYS.get(s[5:].strip())
        if weekday is not None:
            delta = (weekday - today.weekday() + 7) % 7
            return today + timedelta(days=7 if delta == 0 else delta)

    weekday = WEEKDAYS.get(s)
    if weekday is not None:
        delta = (weekday - today.weekday() + 7) % 7
        return today + timedelta(days=delta)

    return None


def parse_deadline_input(raw: str) -> tuple[date | None, time | None] | None:
    s = raw.strip()
    if not s:
        return (None, None)

    parts = s.split()
    if len(parts) >= 2:
        maybe_time = parse_time_token(parts[-1])
        if maybe_time is not None:
            d = parse_natural_date(" ".join(parts[:-1]))
            if d is None:
                return None
            return (d, maybe_time)

    d = parse_natural_date(s)
    if d is None:
        return None
    return (d, None)


def parse_tags_csv(raw: str) -> list[str]:
    tags: list[str] = []
    for item in raw.split(","):
        t = item.strip()
        if not t:
            continue
        if t.startswith("#"):
            t = t[1:]
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    return tags


def parse_attendees_csv(raw: str) -> list[str]:
    out: list[str] = []
    for item in raw.split(","):
        email = item.strip()
        if not email:
            continue
        if email.lower() not in [x.lower() for x in out]:
            out.append(email)
    return out


def format_deadline(deadline: date | None, deadline_time: time | None) -> str:
    if deadline is None:
        return ""
    if deadline_time is None:
        return deadline.isoformat()
    return f"{deadline.isoformat()} {deadline_time.strftime('%H:%M')}"


def _extract_trailing_deadline(title: str) -> tuple[str, date | None, time | None]:
    words = title.split()
    if len(words) < 2:
        return title, None, None

    for start in range(1, len(words)):
        candidate = " ".join(words[start:]).strip(".,")
        parsed = parse_deadline_input(candidate)
        if parsed is None:
            continue
        deadline, deadline_time = parsed
        clean_title = " ".join(words[:start]).strip()
        if clean_title:
            return clean_title, deadline, deadline_time
    return title, None, None


def parse_quick_capture(raw: str) -> CapturedTask:
    # Format: "title ; due=tomorrow 14:00 ; tags=work,urgent ; attendees=a@x.com,b@y.com"
    chunks = [chunk.strip() for chunk in raw.split(";") if chunk.strip()]
    if not chunks:
        return CapturedTask("Untitled", None, None, None, [], [])

    title = chunks[0]
    deadline: date | None = None
    deadline_time: time | None = None
    recurrence_rule: str | None = None
    tags: list[str] = []
    attendees: list[str] = []

    title, recurrence_rule = _extract_inline_recurrence(title)

    # Inline hashtags in title
    inline_tags = [tok[1:] for tok in title.split() if tok.startswith("#") and len(tok) > 1]
    if inline_tags:
        tags.extend(parse_tags_csv(",".join(inline_tags)))
        title = " ".join(tok for tok in title.split() if not tok.startswith("#")).strip() or "Untitled"

    # Inline attendee tokens in title: @email
    inline_attendees = [
        tok[1:] for tok in title.split() if tok.startswith("@") and "@" in tok[1:] and len(tok) > 2
    ]
    if inline_attendees:
        attendees.extend(parse_attendees_csv(",".join(inline_attendees)))
        title = " ".join(tok for tok in title.split() if not tok.startswith("@")).strip() or "Untitled"

    for directive in chunks[1:]:
        if "=" not in directive:
            continue
        key, value = directive.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"due", "deadline"}:
            parsed = parse_deadline_input(value)
            if parsed is not None:
                deadline, deadline_time = parsed
        elif key == "tags":
            tags.extend(parse_tags_csv(value))
        elif key in {"attendees", "people"}:
            attendees.extend(parse_attendees_csv(value))
        elif key in {"repeat", "recurrence"}:
            recurrence_rule = normalize_repeat_rule(value) or recurrence_rule

    # Convenience suffix: "... due tomorrow 14:00"
    if deadline is None:
        m = re.search(r"\b(?:due|by)\s+(.+)$", title, flags=re.IGNORECASE)
        if m:
            parsed = parse_deadline_input(m.group(1))
            if parsed is not None:
                deadline, deadline_time = parsed
                title = title[: m.start()].strip() or "Untitled"

    # Convenience suffix: "... tomorrow", "... next friday 9am", "... in 2 days"
    if deadline is None:
        extracted_title, extracted_deadline, extracted_deadline_time = _extract_trailing_deadline(title)
        if extracted_deadline is not None:
            title = extracted_title
            deadline = extracted_deadline
            deadline_time = extracted_deadline_time

    return CapturedTask(
        title=title or "Untitled",
        deadline=deadline,
        deadline_time=deadline_time,
        recurrence_rule=recurrence_rule,
        tags=parse_tags_csv(",".join(tags)),
        attendees=parse_attendees_csv(",".join(attendees)),
    )
