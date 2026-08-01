from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as dateparser

from seminar_event import SeminarEvent, clean_text


PROGRAM = "Leukemia Research Meeting"


def parse_event_date(
    date_text: str,
    timezone_name: str,
) -> datetime | None:
    """
    Parse dates such as 'Aug 20' that omit the year.

    The current year is assumed. If that date would be more than about
    six months in the past, use the following year instead.
    """
    date_text = clean_text(date_text)

    if not date_text:
        return None

    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)

    try:
        parsed = dateparser.parse(
            date_text,
            fuzzy=True,
            default=now.replace(
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ),
        )
    except (ValueError, TypeError, OverflowError):
        return None

    if parsed is None:
        return None

    parsed = parsed.replace(tzinfo=timezone)

    # Handle schedules that cross into the next calendar year.
    if parsed < now - timedelta(days=180):
        parsed = parsed.replace(year=parsed.year + 1)

    return parsed


def parse_time_range(
    day_time_text: str,
    event_date: datetime,
) -> tuple[datetime, datetime]:
    """
    Parse strings such as:

        Thursday, 11am-12pm
        Thursday, 11:00 am - 12:00 pm
    """
    day_time_text = clean_text(day_time_text)

    match = re.search(
        r"(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)"
        r"\s*[-–—]\s*"
        r"(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)",
        day_time_text,
        re.IGNORECASE,
    )

    if not match:
        raise ValueError(
            f"Could not parse meeting time: {day_time_text}"
        )

    start_text, end_text = match.groups()

    start_clock = dateparser.parse(
        start_text,
        fuzzy=True,
    )
    end_clock = dateparser.parse(
        end_text,
        fuzzy=True,
    )

    if start_clock is None or end_clock is None:
        raise ValueError(
            f"Could not parse meeting time: {day_time_text}"
        )

    start = event_date.replace(
        hour=start_clock.hour,
        minute=start_clock.minute,
        second=0,
        microsecond=0,
    )

    end = event_date.replace(
        hour=end_clock.hour,
        minute=end_clock.minute,
        second=0,
        microsecond=0,
    )

    if end <= start:
        end += timedelta(days=1)

    return start, end


def should_skip_row(
    date_text: str,
    topic: str,
    speaker: str,
) -> bool:
    combined = " ".join(
        [
            clean_text(date_text),
            clean_text(topic),
            clean_text(speaker),
        ]
    )

    return bool(
        re.search(
            r"\b("
            r"cancelled|canceled|break|holiday|"
            r"no meeting|no seminar"
            r")\b",
            combined,
            re.IGNORECASE,
        )
    )


def fetch_leukemia_research_events(
    timezone_name: str,
    source_url: str,
) -> list[SeminarEvent]:
    response = requests.get(
        source_url,
        timeout=60,
    )
    response.raise_for_status()

    reader = csv.DictReader(
        io.StringIO(response.text)
    )

    events: list[SeminarEvent] = []

    for row_number, row in enumerate(
        reader,
        start=2,
    ):
        date_text = clean_text(
            row.get("Date", "")
        )
        day_time_text = clean_text(
            row.get("Day & Time", "")
        )
        location = clean_text(
            row.get("Location", "")
        )
        zoom_url = clean_text(
            row.get("Zoom Link", "")
        )
        topic = clean_text(
            row.get("Topic", "")
        )
        speaker = clean_text(
            row.get("Speaker", "")
        )
        affiliation = clean_text(
            row.get("Affiliation", "")
        )
        speaker_email = clean_text(
            row.get("Speaker Email", "")
        )

        if not date_text or not speaker:
            continue

        if should_skip_row(
            date_text,
            topic,
            speaker,
        ):
            continue

        event_date = parse_event_date(
            date_text,
            timezone_name,
        )

        if event_date is None:
            print(
                "Leukemia Research Meeting: "
                f"skipping row {row_number}; "
                f"could not parse date '{date_text}'"
            )
            continue

        try:
            start, end = parse_time_range(
                day_time_text,
                event_date,
            )
        except ValueError as exc:
            print(
                "Leukemia Research Meeting: "
                f"skipping row {row_number}; {exc}"
            )
            continue

        # Do not display placeholder topics as talk titles.
        talk_title = (
            ""
            if topic.casefold() in {"tbd", "tba", "to be determined"}
            else topic
        )

        source_event_id = (
            f"{start.date().isoformat()}:"
            f"{start.strftime('%H%M')}:"
            f"{speaker.casefold()}"
        )

        description_parts = []

        if speaker_email:
            description_parts.append(
                f"Speaker email: {speaker_email}"
            )

        events.append(
            SeminarEvent(
                source="leukemia_research",
                source_event_id=source_event_id,
                program=PROGRAM,
                speaker=speaker,
                talk_title=talk_title,
                affiliation=affiliation,
                start=start,
                end=end,
                location=location,
                zoom_urls=[zoom_url] if zoom_url else [],
                description="\n".join(
                    description_parts
                ),
                source_url=source_url,
                raw_title=(
                    f"{PROGRAM} - {speaker}"
                    + (
                        f" - {talk_title}"
                        if talk_title
                        else ""
                    )
                ),
                metadata={
                    "speaker_email": speaker_email,
                    "source_row": row_number,
                },
            )
        )

    print(
        "Leukemia Research Meeting: "
        f"parsed {len(events)} events"
    )

    return events
