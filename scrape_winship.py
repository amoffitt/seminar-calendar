#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright

INDEX_URL = "https://winshipcancer.emory.edu/about-us/events.php"
EVENT_URL_RE = re.compile(r"https?://apps\.winshipcancer\.emory\.edu/admin/Event/\d+", re.I)
OUTPUT_PATH = Path("docs/seminars.ics")
DEBUG_DIR = Path("debug")
TIMEZONE = "America/New_York"


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def get_label_value(soup: BeautifulSoup, label: str) -> str:
    """Find text following a visible field label such as Date or Location."""
    wanted = label.casefold()

    for node in soup.find_all(string=True):
        if clean(str(node)).casefold() != wanted:
            continue

        parent = node.parent
        # Search subsequent elements, stopping at another known field label.
        for nxt in parent.find_all_next():
            text = clean(nxt.get_text(" ", strip=True))
            if not text or text.casefold() == wanted:
                continue
            if text.casefold() in {
                "date", "presenter", "location", "event type",
                "description", "details", "contact"
            }:
                break
            return text
    return ""


def parse_date_range(raw: str) -> tuple[datetime, datetime]:
    """
    Parse common Winship date formats, including:
      July 08, 2026 12:00:00 PM - 01:00:00 PM
      August 21, 2026 08/21/2026 07:00:00 PM - 08/22/2026 04:30:00 PM
    """
    raw = clean(raw).replace("Add to Calendar", "").strip()

    if " - " not in raw:
        start = dateparser.parse(raw, fuzzy=True)
        return start, start

    left, right = raw.split(" - ", 1)
    start = dateparser.parse(left, fuzzy=True)

    try:
        end = dateparser.parse(right, fuzzy=True, default=start)
    except Exception:
        end = start

    # If the right side contains only a clock time and parsed earlier than start,
    # treat it as later the same day unless it clearly crosses midnight.
    if end < start and not re.search(r"\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\s+\d{1,2}", right):
        end = end.replace(year=start.year, month=start.month, day=start.day)

    return start, end


def discover_event_urls(page) -> list[str]:
    page.goto(INDEX_URL, wait_until="networkidle", timeout=90000)
    page.wait_for_timeout(4000)

    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / "index.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(DEBUG_DIR / "index.png"), full_page=True)

    urls: set[str] = set()

    for href in page.locator("a").evaluate_all(
        "(els) => els.map(a => a.href).filter(Boolean)"
    ):
        if EVENT_URL_RE.match(href):
            urls.add(href)

    # Also inspect raw rendered HTML in case links are embedded in scripts/data.
    urls.update(EVENT_URL_RE.findall(page.content()))

    return sorted(urls)


def parse_event(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(1000)

    soup = BeautifulSoup(page.content(), "html.parser")
    h1 = soup.find("h1")
    title = clean(h1.get_text(" ", strip=True) if h1 else "")

    date_raw = get_label_value(soup, "Date")
    location = get_label_value(soup, "Location")
    presenter = get_label_value(soup, "Presenter")
    event_type = get_label_value(soup, "Event Type")
    description = get_label_value(soup, "Description")

    if not title or not date_raw:
        raise ValueError(f"Missing title or date on {url}")

    start, end = parse_date_range(date_raw)

    return {
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
    cal = Calendar()
    cal.add("prodid", "-//Winship Seminar Calendar//github.com//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Winship Seminars")
    cal.add("x-wr-timezone", TIMEZONE)

    now = datetime.utcnow()

    for item in sorted(events, key=lambda x: x["start"]):
        event = Event()
        uid = hashlib.sha256(item["url"].encode()).hexdigest() + "@winship-events"
        event.add("uid", uid)
        event.add("summary", item["title"])
        event.add("dtstart", item["start"])
        event.add("dtend", item["end"])
        event.add("dtstamp", now)

        if item["location"]:
            event.add("location", item["location"])

        details = []
        if item["presenter"]:
            details.append(f"Presenter: {item['presenter']}")
        if item["event_type"]:
            details.append(f"Event type: {item['event_type']}")
        if item["description"]:
            details.append(item["description"])
        details.append(item["url"])
        event.add("description", "\n\n".join(details))
        event.add("url", item["url"])

        cal.add_component(event)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(cal.to_ical())


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        urls = discover_event_urls(page)
        print(f"Found {len(urls)} event links")

        events = []
        failures = []

        for i, url in enumerate(urls, start=1):
            try:
                event = parse_event(page, url)
                events.append(event)
                print(f"[{i}/{len(urls)}] {event['start']:%Y-%m-%d} | {event['title']}")
            except Exception as exc:
                failures.append((url, str(exc)))
                print(f"[{i}/{len(urls)}] FAILED {url}: {exc}")

        browser.close()

    write_calendar(events)
    print(f"Wrote {len(events)} events to {OUTPUT_PATH}")

    if failures:
        print(f"{len(failures)} pages could not be parsed:")
        for url, error in failures:
            print(f"  {url}: {error}")

    if not urls:
        raise RuntimeError(
            "No event links were found. Inspect debug/index.html and debug/index.png "
            "in the workflow artifacts."
        )


if __name__ == "__main__":
    main()
