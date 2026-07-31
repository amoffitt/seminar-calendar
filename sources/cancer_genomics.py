from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from seminar_event import SeminarEvent, clean_text


PROGRAM = "Cancer Genetics Club"
LOCATION = "HSRB-II, 6th floor event space N600"
START_HOUR = 16
END_HOUR = 17


def parse_full_date(value: str) -> datetime | None:
    value = clean_text(value).rstrip("*")

    # Require a full month/day/year date. This intentionally skips rows
    # such as "July" and all undated explanatory text.
    if not re.search(r"\b20\d{2}\b", value):
        return None

    try:
        return dateparser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None


def excluded_row(presentation: str, lab: str) -> bool:
    combined = f"{presentation} {lab}"
    return bool(
        re.search(
            r"\b(break|cancel|cancelled|canceled|holiday)\b",
            combined,
            re.IGNORECASE,
        )
    )


def find_lineup_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        text = clean_text(table.get_text(" ", strip=True))
        if (
            "Dates" in text
            and ("Title/Person Presenting" in text or "Person Presenting" in text)
            and "Lab" in text
        ):
            # Prefer the current lineup rather than the previous-meetings table.
            first_date_years = re.findall(r"\b20\d{2}\b", text)
            if first_date_years and max(map(int, first_date_years)) >= 2026:
                return table
    return None


def fetch_cancer_genomics_events(
    timezone_name: str,
    source_url: str,
) -> list[SeminarEvent]:
    response = requests.get(source_url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    table = find_lineup_table(soup)
    if table is None:
        raise ValueError("Could not find the Cancer Genetics Club lineup table")

    tz = ZoneInfo(timezone_name)
    events: list[SeminarEvent] = []

    for row in table.find_all("tr"):
        cells = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]

        if len(cells) < 2:
            continue

        date_value = parse_full_date(cells[0])
        if date_value is None:
            continue

        presentation = cells[1] if len(cells) > 1 else ""
        lab = cells[2] if len(cells) > 2 else ""

        if not presentation or excluded_row(presentation, lab):
            continue

        start = datetime(
            date_value.year,
            date_value.month,
            date_value.day,
            START_HOUR,
            0,
            tzinfo=tz,
        )
        end = start.replace(hour=END_HOUR)

        # The source mixes person names and talk titles in one column.
        # Preserve that text as the speaker field so it remains prominent
        # in the compact calendar view.
        source_id = (
            f"{date_value.date().isoformat()}:{presentation}:{lab}"
        )

        events.append(
            SeminarEvent(
                source="cancer_genomics",
                source_event_id=source_id,
                program=PROGRAM,
                speaker=presentation,
                affiliation=lab,
                start=start,
                end=end,
                location=LOCATION,
                description=(
                    "The source document uses one shared column for the "
                    "talk title or presenting person."
                ),
                source_url=source_url,
                raw_title=f"{PROGRAM} - {presentation}",
            )
        )

    print(f"Cancer Genetics Club: parsed {len(events)} events")
    return events
