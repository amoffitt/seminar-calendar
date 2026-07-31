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
from playwright.sync_api import Locator, Page, sync_playwright


INDEX_URL = "https://winshipcancer.emory.edu/about-us/events.php"

EVENT_URL_RE = re.compile(
    r"https?://apps\.winshipcancer\.emory\.edu/admin/Event/\d+[^\"'<>\s]*",
    re.IGNORECASE,
)

ZOOM_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?zoom\.us/[^\s\"'<>]+",
    re.IGNORECASE,
)

OUTPUT_PATH = Path("docs/seminars.ics")
DEBUG_DIR = Path("debug")

TIMEZONE = "America/New_York"
LOCAL_TZ = ZoneInfo(TIMEZONE)

MAX_LISTING_PAGES = 50


def clean(text: str | None) -> str:
    """Collapse repeated whitespace and remove leading/trailing spaces."""
    return re.sub(r"\s+", " ", text or "").strip()


def get_label_value(soup: BeautifulSoup, label: str) -> str:
    """
    Find text following a visible field label such as Date, Location,
    Presenter, Event Type, or Description.
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
    match = re.search(
        r"/admin/Event/(\d+)",
        url,
        re.IGNORECASE,
    )

    if not match:
        return None

    return match.group(1)


def canonicalize_event_urls(urls: set[str]) -> list[str]:
    """
    Keep only one URL per Winship event ID.

    Winship commonly exposes both:

      /admin/Event/6735
      /admin/Event/6735/descriptive-event-title/

    Prefer the longer descriptive URL.
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
    """Attach the configured local timezone to a naive datetime."""
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

        # If the end contains only a clock time, dateutil inherits the
        # start date. If it is earlier than the start, treat it as overnight.
        if end < start:
            end += timedelta(days=1)

    else:
        # Give events with only one timestamp a default duration of one hour.
        end = start + timedelta(hours=1)

    return start, end


def normalize_zoom_url(url: str) -> str:
    """
    Remove punctuation that may have been captured after a Zoom URL.
    """
    return url.rstrip(".,;:)]}\"'")


def extract_zoom_links(soup: BeautifulSoup) -> list[str]:
    """
    Return unique Zoom URLs found in links or rendered page text.
    """
    zoom_links: list[str] = []

    def add_zoom_url(url: str) -> None:
        normalized = normalize_zoom_url(url)

        if normalized not in zoom_links:
            zoom_links.append(normalized)

    # Normal clickable links.
    for anchor in soup.find_all("a", href=True):
        href = clean(anchor.get("href"))

        if not href:
            continue

        absolute_url = urljoin(INDEX_URL, href)

        if ZOOM_URL_RE.match(absolute_url):
            add_zoom_url(absolute_url)

    # Also inspect raw page text/HTML in case the URL is not in an <a> tag.
    for match in ZOOM_URL_RE.findall(str(soup)):
        add_zoom_url(match)

    return zoom_links


def collect_urls_from_current_listing_page(
    page: Page,
    urls: set[str],
) -> None:
    """Collect event-detail URLs from the currently displayed listing page."""
    rendered_html = page.content()

    hrefs = page.locator("a").evaluate_all(
        "(elements) => elements.map(a => a.href).filter(Boolean)"
    )

    for href in hrefs:
        absolute_url = urljoin(INDEX_URL, href)

        if extract_event_id(absolute_url):
            urls.add(absolute_url)

    # Also inspect raw HTML in case URLs are embedded in scripts,
    # JSON objects, or data attributes.
    for match in EVENT_URL_RE.findall(rendered_html):
        urls.add(match)


def save_listing_debug_files(
    page: Page,
    page_number: int,
) -> None:
    """Save the rendered HTML and screenshot for one listing page."""
    DEBUG_DIR.mkdir(exist_ok=True)

    rendered_html = page.content()

    (DEBUG_DIR / f"index-page-{page_number}.html").write_text(
        rendered_html,
        encoding="utf-8",
    )

    page.screenshot(
        path=str(DEBUG_DIR / f"index-page-{page_number}.png"),
        full_page=True,
    )


def locator_is_disabled(locator: Locator) -> bool:
    """Determine whether a pagination control is disabled."""
    aria_disabled = locator.get_attribute("aria-disabled")
    disabled_attr = locator.get_attribute("disabled")
    class_name = clean(locator.get_attribute("class")).lower()

    return (
        aria_disabled == "true"
        or disabled_attr is not None
        or "disabled" in class_name
    )


def find_next_page_control(page: Page) -> Locator | None:
    """
    Find a likely next-page control.

    The function tries several common accessibility labels, link texts,
    button texts, CSS classes, and rel="next".
    """
    candidates: list[Locator] = [
        page.locator('a[rel="next"]'),
        page.locator('button[rel="next"]'),
        page.get_by_role(
            "link",
            name=re.compile(
                r"^\s*(next|next page|›|»)\s*$",
                re.IGNORECASE,
            ),
        ),
        page.get_by_role(
            "button",
            name=re.compile(
                r"^\s*(next|next page|›|»)\s*$",
                re.IGNORECASE,
            ),
        ),
        page.locator(
            "a.next, button.next, "
            "a.pagination-next, button.pagination-next, "
            ".pagination a[aria-label*='Next' i], "
            ".pagination button[aria-label*='Next' i]"
        ),
    ]

    for candidate in candidates:
        if candidate.count() > 0:
            return candidate.first

    return None


def listing_page_signature(page: Page) -> str:
    """
    Create a signature representing the currently displayed event links.

    This is more reliable than comparing the full HTML because some pages
    contain dynamic timestamps or rotating elements.
    """
    event_ids: list[str] = []

    hrefs = page.locator("a").evaluate_all(
        "(elements) => elements.map(a => a.href).filter(Boolean)"
    )

    for href in hrefs:
        event_id = extract_event_id(href)

        if event_id:
            event_ids.append(event_id)

    return "|".join(sorted(set(event_ids), key=int))


def discover_event_urls(page: Page) -> list[str]:
    """
    Load every page of the Winship event listing and collect unique
    event-detail URLs.
    """
    page.goto(
        INDEX_URL,
        wait_until="networkidle",
        timeout=90_000,
    )

    page.wait_for_timeout(4_000)

    urls: set[str] = set()
    page_number = 1
    seen_page_signatures: set[str] = set()

    while True:
        print(f"Reading listing page {page_number}")

        save_listing_debug_files(
            page,
            page_number,
        )

        collect_urls_from_current_listing_page(
            page,
            urls,
        )

        current_signature = listing_page_signature(page)

        if current_signature:
            if current_signature in seen_page_signatures:
                print(
                    "Listing page contents repeated; stopping to avoid "
                    "an infinite pagination loop"
                )
                break

            seen_page_signatures.add(current_signature)

        next_control = find_next_page_control(page)

        if next_control is None:
            print("No next-page control found")
            break

        if locator_is_disabled(next_control):
            print("Reached final listing page")
            break

        previous_signature = current_signature

        try:
            next_control.scroll_into_view_if_needed()
            next_control.click(timeout=30_000)
        except Exception as exc:
            print(f"Could not click next-page control: {exc}")
            break

        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=30_000,
            )
        except Exception:
            # AJAX-heavy pages may never reach strict network idle.
            pass

        page.wait_for_timeout(2_000)

        new_signature = listing_page_signature(page)

        if new_signature == previous_signature:
            print(
                "Next-page control did not change the visible event links; "
                "stopping to avoid an infinite loop"
            )
            break

        page_number += 1

        if page_number > MAX_LISTING_PAGES:
            raise RuntimeError(
                f"Pagination exceeded {MAX_LISTING_PAGES} pages; "
                "stopping as a safety measure."
            )

    return canonicalize_event_urls(urls)


def parse_event(page: Page, url: str) -> dict:
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
    zoom_links = extract_zoom_links(soup)

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
        "zoom_links": zoom_links,
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

        calendar_event.add(
            "dtstart",
            item["start"].astimezone(UTC),
        )

        calendar_event.add(
            "dtend",
            item["end"].astimezone(UTC),
        )
        calendar_event.add("dtstamp", now)
        calendar_event.add("status", "CONFIRMED")
        calendar_event.add("transp", "OPAQUE")

        if item["location"]:
            calendar_event.add(
                "location",
                item["location"],
            )
        elif item["zoom_links"]:
            calendar_event.add(
                "location",
                "Zoom",
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

        if item["zoom_links"]:
            if len(item["zoom_links"]) == 1:
                details.append(
                    f"Join via Zoom: {item['zoom_links'][0]}"
                )
            else:
                zoom_text = "\n".join(
                    item["zoom_links"]
                )
                details.append(
                    f"Zoom links:\n{zoom_text}"
                )


        details.append(
            f"Winship event page: {item['url']}"
        )

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

                zoom_status = (
                    f" | {len(event['zoom_links'])} Zoom link(s)"
                    if event["zoom_links"]
                    else ""
                )

                print(
                    f"[{index}/{len(urls)}] "
                    f"{event['start']:%Y-%m-%d %H:%M} | "
                    f"{event['title']}"
                    f"{zoom_status}"
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

    total_zoom_events = sum(
        1
        for event in events
        if event["zoom_links"]
    )

    print(
        f"Wrote {len(events)} events to "
        f"{OUTPUT_PATH}"
    )

    print(
        f"Found Zoom links for "
        f"{total_zoom_events} of {len(events)} events"
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
            "No event links were found. Inspect the listing-page "
            "HTML and screenshots in the debug workflow artifact."
        )


if __name__ == "__main__":
    main()
