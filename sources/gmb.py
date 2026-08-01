from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as dateparser

from seminar_event import SeminarEvent, clean_text


PROGRAM = "GMB Seminar"
LOCATION = "Whitehead Auditorium"
START_HOUR = 12
END_HOUR = 13


def parse_date(value: str) -> datetime | None:
    value = clean_text(value)
    if not re.search(r"\b20\d{2}\b", value):
        return None

    try:
        return dateparser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None


def value_at(row: list[str], index: int) -> str:
    return clean_text(row[index]) if index < len(row) else ""


def is_excluded_label(value: str) -> bool:
    value = clean_text(value)

    return bool(
        re.search(
            r"\b("
            r"fall break|summer break|winter break|thanksgiving break|"
            r"make-up|tba|cancelled|canceled|"
            r"whitehead auditorium|location|date|speaker|student speaker|"
            r"talk title|advisor|confirmed"
            r")\b",
            value,
            re.IGNORECASE,
        )
    )

def make_event(
    *,
    date_value: datetime,
    slot_label: str,
    speaker: str,
    talk_title: str,
    advisor: str,
    confirmed: str,
    source_url: str,
    timezone_name: str,
) -> SeminarEvent | None:
    speaker = clean_text(speaker)
    talk_title = clean_text(talk_title)
    advisor = clean_text(advisor)
    confirmed = clean_text(confirmed)

    if not speaker or is_excluded_label(speaker):
        return None

    if speaker.casefold() == LOCATION.casefold():
        return None

    tz = ZoneInfo(timezone_name)
    start = datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        START_HOUR,
        0,
        tzinfo=tz,
    )
    end = start.replace(hour=END_HOUR)

    source_id = (
        f"{date_value.date().isoformat()}:{slot_label}:{speaker}"
    )

    metadata = {
        "slot": slot_label,
        "confirmed": confirmed,
    }

    return SeminarEvent(
        source="gmb",
        source_event_id=source_id,
        program=PROGRAM,
        speaker=speaker,
        talk_title=talk_title,
        affiliation=f"Advisor: {advisor}" if advisor else "",
        start=start,
        end=end,
        location=LOCATION,
        description=(
            f"Presentation slot: {slot_label}"
            + (f"\nConfirmation status: {confirmed}" if confirmed else "")
        ),
        source_url=source_url,
        raw_title=f"{PROGRAM} - {speaker}",
        metadata=metadata,
    )


def fetch_gmb_events(
    timezone_name: str,
    source_url: str,
) -> list[SeminarEvent]:
    response = requests.get(source_url, timeout=60)
    response.raise_for_status()

    rows = list(
        csv.reader(
            io.StringIO(response.text)
        )
    )

    events: list[SeminarEvent] = []
    index = 0

    while index < len(rows):
        row = rows[index]
        date_value = parse_date(value_at(row, 0))

        if date_value is None:
            index += 1
            continue

        row_label = value_at(row, 1)
        first_speaker = value_at(row, 2)

        # Faculty-speaker rows have one event and no subordinate title/advisor rows.
        if "faculty speaker" in row_label.casefold():
            if first_speaker and not is_excluded_label(first_speaker):
                tz = ZoneInfo(timezone_name)
                start = datetime(
                    date_value.year,
                    date_value.month,
                    date_value.day,
                    START_HOUR,
                    0,
                    tzinfo=tz,
                )
                end = start.replace(hour=END_HOUR)

                events.append(
                    SeminarEvent(
                        source="gmb",
                        source_event_id=(
                            f"{date_value.date().isoformat()}:faculty:{first_speaker}"
                        ),
                        program="GMB Faculty Seminar",
                        speaker=first_speaker,
                        start=start,
                        end=end,
                        location=LOCATION,
                        source_url=source_url,
                        raw_title=f"GMB Faculty Seminar - {first_speaker}",
                    )
                )

            index += 1
            continue

        # Skip non-event date rows such as Fall Break.
        if is_excluded_label(row_label) or is_excluded_label(first_speaker):
            index += 1
            continue

        # Student rows are followed by Talk Title and Advisor rows.
        title_row = rows[index + 1] if index + 1 < len(rows) else []
        advisor_row = rows[index + 2] if index + 2 < len(rows) else []

        if value_at(title_row, 1).casefold() != "talk title:":
            title_row = []
        if value_at(advisor_row, 1).casefold() != "advisor:":
            advisor_row = []

        slot1 = make_event(
            date_value=date_value,
            slot_label="Slot 1",
            speaker=value_at(row, 2),
            confirmed=value_at(row, 3),
            talk_title=value_at(title_row, 2),
            advisor=value_at(advisor_row, 2),
            source_url=source_url,
            timezone_name=timezone_name,
        )
        if slot1:
            events.append(slot1)

        slot2 = make_event(
            date_value=date_value,
            slot_label="Slot 2",
            speaker=value_at(row, 5),
            confirmed=value_at(row, 6),
            talk_title=value_at(title_row, 5),
            advisor=value_at(advisor_row, 5),
            source_url=source_url,
            timezone_name=timezone_name,
        )
        if slot2:
            events.append(slot2)

        index += 3

    print(f"GMB: parsed {len(events)} events")
    return events
