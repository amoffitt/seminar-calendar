from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as dateparser

from seminar_event import SeminarEvent, clean_text


URL = (
    "https://med.emory.edu/departments/human-genetics/"
    "upcoming_grandrounds_seminars.html"
)

SECTION_NAMES = {
    "Seminars",
    "Steven T. Warren Distinguished Lecture",
    "Faculty Career Path Talk",
    "Research in Progress",
    "Conversations with Emory Leaders",
    "Early Career Seminar",
    "Grand Rounds",
    "Clinical Conference",
}


def date_from_text(value: str) -> datetime | None:
    match = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", value)
    if not match:
        return None
    return dateparser.parse(match.group(0), dayfirst=False)


def section_duration(section: str) -> tuple[int, int]:
    if section in {"Grand Rounds", "Clinical Conference"}:
        return 8, 9
    return 12, 13


def zoom_links_from_page(soup: BeautifulSoup) -> dict[str, str]:
    zooms: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        label = clean_text(anchor.get_text(" ", strip=True))
        if "zoom.us" not in href:
            continue
        if label in {"Seminar", "Grand Rounds"}:
            zooms["seminar_grand_rounds"] = href
        elif label == "Clinical Conference":
            zooms["clinical_conference"] = href
    return zooms


def iter_table_rows(soup: BeautifulSoup):
    """
    Yield (section, cells) from the calendar table.

    The page's first column sometimes contains section labels in rows that do
    not contain a date, followed by ordinary four-column event rows.
    """
    current_section = ""

    for row in soup.find_all("tr"):
        cells = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"])
        ]
        if not cells:
            continue

        joined = " | ".join(cells)

        section_hit = next(
            (name for name in SECTION_NAMES if name.casefold() == joined.casefold()),
            None,
        )
        if section_hit:
            current_section = section_hit
            continue

        # Some CMS tables render section labels alongside empty cells.
        if cells[0] in SECTION_NAMES and date_from_text(joined) is None:
            current_section = cells[0]
            continue

        if date_from_text(cells[0] if cells else ""):
            yield current_section or "Human Genetics Event", cells, row


def fetch_human_genetics_events(timezone_name: str) -> list[SeminarEvent]:
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    zooms = zoom_links_from_page(soup)
    tz = ZoneInfo(timezone_name)

    events: list[SeminarEvent] = []

    for section, cells, row in iter_table_rows(soup):
        while len(cells) < 4:
            cells.append("")

        raw_date, host, speaker, affiliation = cells[:4]
        date_value = date_from_text(raw_date)
        if date_value is None:
            continue

        start_hour, end_hour = section_duration(section)
        start = datetime(
            date_value.year,
            date_value.month,
            date_value.day,
            start_hour,
            0,
            tzinfo=tz,
        )
        end = start.replace(hour=end_hour)

        is_virtual = "VIRTUAL" in raw_date.upper()
        location = (
            "Virtual"
            if is_virtual
            else "Whitehead Biomedical Research Building, Suite 300"
        )

        if section == "Clinical Conference":
            zoom_url = zooms.get("clinical_conference", "")
        else:
            zoom_url = zooms.get("seminar_grand_rounds", "")

        source_id = f"{section}:{date_value.date().isoformat()}:{speaker}"
        raw_title = f"{section} - {speaker}".strip(" -")

        events.append(
            SeminarEvent(
                source="human_genetics",
                source_event_id=source_id,
                program=section,
                speaker=speaker,
                affiliation=affiliation,
                host=re.sub(r"^(Host|Hosts|Mentor):\s*", "", host, flags=re.I),
                start=start,
                end=end,
                location=location,
                zoom_urls=[zoom_url] if zoom_url else [],
                source_url=URL,
                raw_title=raw_title,
            )
        )

    print(f"Human Genetics: parsed {len(events)} events")
    return events
