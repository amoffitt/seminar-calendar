from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as dateparser

from seminar_event import SeminarEvent, clean_text


URL = (
    "https://med.emory.edu/departments/biomedical-informatics/"
    "news-and-seminar/seminar-index.html"
)


def parse_date_line(text: str, timezone_name: str):
    text = clean_text(text)
    text = re.sub(r"^Date:\s*", "", text, flags=re.I)

    # Normalize common formatting, e.g. 12:00PM-1:00PM.
    match = re.search(
        r"(.+?)\|\s*(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*"
        r"(\d{1,2}:\d{2}\s*[AP]M)(?:,\s*(.*))?$",
        text,
        re.I,
    )
    if not match:
        return None

    date_text, start_text, end_text, location = match.groups()
    tz = ZoneInfo(timezone_name)

    start = dateparser.parse(f"{date_text} {start_text}", fuzzy=True)
    end = dateparser.parse(
        f"{date_text} {end_text}",
        fuzzy=True,
        default=start,
    )

    if start is None or end is None:
        return None

    start = start.replace(tzinfo=tz)
    end = end.replace(tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)

    return start, end, clean_text(location)


def following_text_until_heading(heading: Tag) -> list[str]:
    lines: list[str] = []
    for node in heading.find_all_next():
        if node is not heading and isinstance(node, Tag) and node.name in {"h1", "h2"}:
            break
        if isinstance(node, Tag) and node.name in {"p", "div", "li"}:
            text = clean_text(node.get_text(" ", strip=True))
            if text and (not lines or text != lines[-1]):
                lines.append(text)
    return lines


def fetch_bmi_events(timezone_name: str) -> list[SeminarEvent]:
    response = requests.get(URL, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    events: list[SeminarEvent] = []

    for heading in soup.find_all("h2"):
        title = clean_text(heading.get_text(" ", strip=True))
        if not title or title in {"2026 Events", "2026 BMI Winter/Spring Seminar Series"}:
            continue

        lines = following_text_until_heading(heading)
        date_line = next((line for line in lines if line.lower().startswith("date:")), "")
        speaker_line = next(
            (line for line in lines if line.lower().startswith("speakers:")),
            "",
        )

        parsed = parse_date_line(date_line, timezone_name)
        if parsed is None:
            continue

        start, end, location = parsed
        speaker = re.sub(r"^Speakers:\s*", "", speaker_line, flags=re.I).strip()

        abstract_lines: list[str] = []
        affiliation_lines: list[str] = []
        seen_speaker = False

        for line in lines:
            if line == speaker_line:
                seen_speaker = True
                continue
            if line.lower().startswith("abstract:"):
                abstract_lines.append(re.sub(r"^Abstract:\s*", "", line, flags=re.I))
                continue
            if abstract_lines:
                abstract_lines.append(line)
            elif seen_speaker and not line.lower().startswith("date:"):
                affiliation_lines.append(line)

        link = heading.find("a", href=True)
        source_url = link["href"] if link else URL
        source_id = f"{start.isoformat()}:{title}"

        events.append(
            SeminarEvent(
                source="bmi",
                source_event_id=source_id,
                program="BMI Seminar",
                speaker=speaker,
                talk_title=title,
                affiliation="; ".join(affiliation_lines[:3]),
                start=start,
                end=end,
                location=location,
                description=" ".join(abstract_lines),
                source_url=source_url,
                raw_title=title,
            )
        )

    print(f"BMI: parsed {len(events)} events")
    return events
