#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright


INDEX_URL = "https://winshipcancer.emory.edu/about-us/events.php"

EVENT_URL_RE = re.compile(
    r"https?://apps\.winshipcancer\.emory\.edu/admin/Event/\d+[^\"'<>\s]*",
    re.IGNORECASE,
)

OUTPUT_PATH = Path("docs/seminars.ics")
DEBUG_DIR = Path("debug")

TIMEZONE = "America/New_York"
LOCAL_TZ = ZoneInfo(TIMEZONE)


def clean(text: str | None) -> str:
    """Collapse repeated whitespace and remove leading/trailing spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def get_label_value(soup: BeautifulSoup, label: str) -> str:
    """
    Find text following a visible field label such as Date, Location,
    Presenter, or Event Type.
    """
    wanted = label.casefold()

    stop_labels = {
        "date",
        "presenter",
        "location",
        "event type",
        "description",
        "details",
        "contact",
    }

    for node in soup.find_all(string=True):
        if clean(str(node)).casefold() != wanted:
            continue

        parent = node.parent

        for nxt in parent.find_all_next():
            text = clean(nxt.get_text(" ", strip=True))

            if not text:
                continue

            if text.casefold() == wanted:
                continue

            if text.casefold() in stop_labels:
                break

            return text

    return ""


def extract_event_id(url: str) -> str | None:
    """Extract the numeric Winship event ID from an event URL."""
    match = re.search(r"/admin/Event/(\d+)", url, re.IGNORECASE)

    if not match:
        return None

    return match.group(1)


def canonicalize_event_urls(urls: set[str]) -> list[str]:
    """
    Keep only one URL per Winship event ID.

    Winship commonly exposes both:

      /admin/Event/6735
      /admin/Event/6735/descriptive-event-title/

    Prefer the longer, descriptive URL.
    """
    by_event_id: dict[str, str] = {}

    for raw_url in urls:
        url = raw_url.rstrip(".,;\"'")

        event_id = extract_event_id(url)

        if event_id is None:
            continue

        current = by_event_id.get(event_id)

        if current is None or len(url) > len(current):
            by_event_id[event_id] = url

    return sorted(
        by_event_id.values(),
        key=lambda url: int(extract_event_id(url) or 0),
    )


def remove_redundant_long_date(raw: str) -> str:
    """
    Normalize strings where Winship prints the same date twice.

    Example:

      August 04, 2026 08/04/2026 09:00:00 AM

    becomes:

      08/04/2026 09:00:00 AM
    """
    numeric_date = re.search(
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        raw,
    )

    if numeric_date:
        return raw[numeric_date.start():]

    return raw


def ensure_local_timezone(value: datetime) -> datetime:
    """Attach the configured local timezone when parsing returns a naive time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=LOCAL_TZ)

    return value


def parse_date_range(raw: str) -> tuple[datetime, datetime]:
    """
    Parse common Winship date formats.

    Examples:

      July 08, 2026 12:00:00 PM - 01:00:00 PM

      August 04, 2026 08/04/2026 09:00:00 AM

      August 21, 2026
      08/21/2026 07:00:00 PM - 08/22/2026 04:30:00 PM
    """
    raw = clean(raw)
    raw = raw.replace("Add to Calendar", "").strip()
    raw = remove_redundant_long_date(raw)

    if " - " in raw:
        start_text, end_text = raw.split(" - ", 1)
    else:
        start_text = raw
        end_text = None

    start = dateparser.parse(
        start_text,
        fuzzy=True,
    )

    if start is None:
        raise ValueError(f"Could not parse start date: {raw}")

    start = ensure_local_timezone(start)

    if end_text:
        end = dateparser.parse(
            end_text,
            fuzzy=True,
            default=start.replace(tzinfo=None),
        )

        if end is None:
            raise ValueError(f"Could not parse end date: {raw}")

        end = ensure_local_timezone(end)

        # If the end field contains only a time, dateutil inherits the
        # start date. If the resulting time is earlier than the start,
        # treat it as an overnight event.
        if end < start:
            end += timedelta(days=1)

    else:
        # Some events expose only a single timestamp.
        # Give those events a default duration of one hour.
        end = start + timedelta(hours=1)

    return start, end


def discover_event_urls(page) -> list[str]:
    """Load the Winship listing page and collect unique event-detail URLs."""
    page.goto(
        INDEX_URL,
        wait_until="networkidle",
        timeout=90_000,
    )

    page.wait_for_timeout(4_000)

    DEBUG_DIR.mkdir(exist_ok=True)

    rendered_html = page.content()

    (DEBUG_DIR / "index.html").write_text(
        rendered_html,
        encoding="utf-8",
    )

    page.screenshot(
        path=str(DEBUG_DIR / "index.png"),
        full_page=True,
    )

    urls: set[str] = set()

    hrefs = page.locator("a").evaluate_all(
        "(elements) => elements.map(a => a.href).filter(Boolean)"
    )

    for href in hrefs:
        absolute_url = urljoin(INDEX_URL, href)

        if extract_event_id(absolute_url):
            urls.add(absolute_url)

    # Also inspect raw rendered HTML in case URLs are embedded inside
    # scripts, JSON, or data attributes rather than normal anchor tags.
    for match in EVENT_URL_RE.findall(rendered_html):
        urls.add(match)

    return canonicalize_event_urls(urls)


def parse_event(page, url: str) -> dict:
    """Load and parse one Winship event page."""
    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=90_000,
    )

    page.wait_for_timeout(1_000)

    soup = BeautifulSoup(
        page.content(),
        "html.parser",
    )

    h1 = soup.find("h1")

    title = clean(
        h1.get_text(" ", strip=True)
        if h1
        else ""
    )

    date_raw = get_label_value(soup, "Date")
    location = get_label_value(soup, "Location")
    presenter = get_label_value(soup, "Presenter")
    event_type = get_label_value(soup, "Event Type")
    description = get_label_value(soup, "Description")

    if not title:
        raise ValueError(f"Missing title on {url}")

    if not date_raw:
        raise ValueError(f"Missing date on {url}")

    start, end = parse_date_range(date_raw)

    return {
        "event_id": extract_event_id(url),
        "title": title,
        "start": start,
        "end": end,
        "location": location,
        "presenter": presenter,
        "event_type": event_type,
        "description": description,
        "url": url,
    }


def write_calendar(events: list[dict]) -> None:
    """Write parsed events to an Outlook-compatible ICS calendar."""
    calendar = Calendar()

    calendar.add(
        "prodid",
        "-//Winship Seminar Calendar//github.com//",
    )
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Winship Seminars")
    calendar.add("x-wr-timezone", TIMEZONE)

    now = datetime.now(UTC)

    for item in sorted(
        events,
        key=lambda event: event["start"],
    ):
        calendar_event = Event()

        event_id = item["event_id"] or hashlib.sha256(
            item["url"].encode()
        ).hexdigest()

        uid = f"winship-{event_id}@seminar-calendar"

        calendar_event.add("uid", uid)
        calendar_event.add("summary", item["title"])
        calendar_event.add("dtstart", item["start"])
        calendar_event.add("dtend", item["end"])
        calendar_event.add("dtstamp", now)

        if item["location"]:
            calendar_event.add(
                "location",
                item["location"],
            )

        details: list[str] = []

        if item["presenter"]:
            details.append(
                f"Presenter: {item['presenter']}"
            )

        if item["event_type"]:
            details.append(
                f"Event type: {item['event_type']}"
            )

        if item["description"]:
            details.append(
                item["description"]
            )

        details.append(item["url"])

        calendar_event.add(
            "description",
            "\n\n".join(details),
        )

        calendar_event.add(
            "url",
            item["url"],
        )

        calendar.add_component(calendar_event)

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_PATH.write_bytes(
        calendar.to_ical()
    )


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        page = browser.new_page()

        urls = discover_event_urls(page)

        print(
            f"Found {len(urls)} unique event links"
        )

        events: list[dict] = []
        failures: list[tuple[str, str]] = []

        for index, url in enumerate(
            urls,
            start=1,
        ):
            try:
                event = parse_event(
                    page,
                    url,
                )

                events.append(event)

                print(
                    f"[{index}/{len(urls)}] "
                    f"{event['start']:%Y-%m-%d %H:%M} | "
                    f"{event['title']}"
                )

            except Exception as exc:
                failures.append(
                    (url, str(exc))
                )

                print(
                    f"[{index}/{len(urls)}] "
                    f"FAILED {url}: {exc}"
                )

        browser.close()

    write_calendar(events)

    print(
        f"Wrote {len(events)} events to "
        f"{OUTPUT_PATH}"
    )

    if failures:
        print(
            f"{len(failures)} pages could not be parsed:"
        )

        for url, error in failures:
            print(
                f"  {url}: {error}"
            )

    if not urls:
        raise RuntimeError(
            "No event links were found. Inspect "
            "debug/index.html and debug/index.png "
            "in the workflow artifacts."
        )


if __name__ == "__main__":
    main()
