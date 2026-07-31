from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from playwright.sync_api import Locator, Page, sync_playwright

from seminar_event import SeminarEvent, clean_text


INDEX_URL = "https://winshipcancer.emory.edu/about-us/events.php"
EVENT_URL_RE = re.compile(
    r"https?://apps\.winshipcancer\.emory\.edu/admin/Event/\d+[^\"'<>\s]*",
    re.IGNORECASE,
)
ZOOM_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?zoom\.us/[^\s\"'<>]+",
    re.IGNORECASE,
)
DEBUG_DIR = Path("debug/winship")
MAX_LISTING_PAGES = 50


def get_label_value(soup: BeautifulSoup, label: str) -> str:
    wanted = label.casefold()
    stop_labels = {
        "date", "presenter", "location", "event type",
        "description", "details", "contact",
    }

    for node in soup.find_all(string=True):
        if clean_text(str(node)).casefold() != wanted:
            continue
        parent = node.parent
        for nxt in parent.find_all_next():
            text = clean_text(nxt.get_text(" ", strip=True))
            if not text or text.casefold() == wanted:
                continue
            if text.casefold() in stop_labels:
                break
            return text
    return ""


def extract_event_id(url: str) -> str | None:
    match = re.search(r"/admin/Event/(\d+)", url, re.IGNORECASE)
    return match.group(1) if match else None


def canonicalize_event_urls(urls: set[str]) -> list[str]:
    by_id: dict[str, str] = {}
    for raw_url in urls:
        url = raw_url.rstrip(".,;\"'")
        event_id = extract_event_id(url)
        if not event_id:
            continue
        current = by_id.get(event_id)
        if current is None or len(url) > len(current):
            by_id[event_id] = url
    return sorted(by_id.values(), key=lambda url: int(extract_event_id(url) or 0))


def parse_date_range(raw: str, timezone_name: str) -> tuple[datetime, datetime]:
    raw = clean_text(raw).replace("Add to Calendar", "").strip()

    numeric_date = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", raw)
    if numeric_date:
        raw = raw[numeric_date.start():]

    if " - " in raw:
        start_text, end_text = raw.split(" - ", 1)
    else:
        start_text, end_text = raw, None

    local_tz = ZoneInfo(timezone_name)
    start = dateparser.parse(start_text, fuzzy=True)
    if start is None:
        raise ValueError(f"Could not parse date: {raw}")
    if start.tzinfo is None:
        start = start.replace(tzinfo=local_tz)

    if end_text:
        end = dateparser.parse(
            end_text,
            fuzzy=True,
            default=start.replace(tzinfo=None),
        )
        if end is None:
            raise ValueError(f"Could not parse end time: {raw}")
        if end.tzinfo is None:
            end = end.replace(tzinfo=local_tz)
        if end < start:
            end += timedelta(days=1)
    else:
        end = start + timedelta(hours=1)

    return start, end


def extract_zoom_links(soup: BeautifulSoup) -> list[str]:
    links: list[str] = []

    def add(url: str) -> None:
        normalized = url.rstrip(".,;:)]}\"'")
        if normalized not in links:
            links.append(normalized)

    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        absolute = urljoin(INDEX_URL, href)
        if ZOOM_URL_RE.match(absolute):
            add(absolute)

    for match in ZOOM_URL_RE.findall(str(soup)):
        add(match)

    return links


def collect_current_page(page: Page, urls: set[str]) -> None:
    html = page.content()
    hrefs = page.locator("a").evaluate_all(
        "(els) => els.map(a => a.href).filter(Boolean)"
    )
    for href in hrefs:
        absolute = urljoin(INDEX_URL, href)
        if extract_event_id(absolute):
            urls.add(absolute)
    urls.update(EVENT_URL_RE.findall(html))


def signature(page: Page) -> str:
    ids: set[str] = set()
    hrefs = page.locator("a").evaluate_all(
        "(els) => els.map(a => a.href).filter(Boolean)"
    )
    for href in hrefs:
        event_id = extract_event_id(href)
        if event_id:
            ids.add(event_id)
    return "|".join(sorted(ids, key=int))


def find_next(page: Page) -> Locator | None:
    candidates = [
        page.locator('a[rel="next"]'),
        page.locator('button[rel="next"]'),
        page.get_by_role(
            "link",
            name=re.compile(r"^\s*(next|next page|›|»)\s*$", re.I),
        ),
        page.get_by_role(
            "button",
            name=re.compile(r"^\s*(next|next page|›|»)\s*$", re.I),
        ),
        page.locator(
            "a.next, button.next, a.pagination-next, button.pagination-next, "
            ".pagination a[aria-label*='Next' i], "
            ".pagination button[aria-label*='Next' i]"
        ),
    ]
    for candidate in candidates:
        if candidate.count() > 0:
            return candidate.first
    return None


def disabled(locator: Locator) -> bool:
    return (
        locator.get_attribute("aria-disabled") == "true"
        or locator.get_attribute("disabled") is not None
        or "disabled" in clean_text(locator.get_attribute("class")).lower()
    )


def discover_urls(page: Page) -> list[str]:
    page.goto(INDEX_URL, wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(3_000)

    urls: set[str] = set()
    seen: set[str] = set()
    page_number = 1
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        print(f"Winship: reading listing page {page_number}")
        html = page.content()
        (DEBUG_DIR / f"page-{page_number}.html").write_text(html, encoding="utf-8")
        collect_current_page(page, urls)

        current = signature(page)
        if current in seen:
            break
        seen.add(current)

        nxt = find_next(page)
        if nxt is None or disabled(nxt):
            break

        nxt.scroll_into_view_if_needed()
        nxt.click(timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(1_000)

        if signature(page) == current:
            break

        page_number += 1
        if page_number > MAX_LISTING_PAGES:
            raise RuntimeError("Winship pagination exceeded safety limit")

    return canonicalize_event_urls(urls)


def infer_program(raw_title: str, event_type: str) -> str:
    title = clean_text(raw_title)

    known = [
        "Breast Cancer Translational Research Seminar Series",
        "Cell and Molecular Biology (CMB) Program Meeting",
        "Cancer Prevention and Control (CPC) Program Meeting",
        "Cancer Immunology (CI) Program Meeting",
        "Discovery and Developmental Therapeutics (DDT) Program Meeting",
        "Scarborough Grand Rounds",
        "Surgical Grand Rounds",
        "Elkin Lecture",
        "Grand Rounds",
        "Breast Oncology Guest Lecturer",
    ]
    for program in known:
        if program.casefold() in title.casefold():
            return program

    return clean_text(event_type) or "Winship Event"


def split_title(raw_title: str, presenter: str, program: str) -> tuple[str, str]:
    speaker = clean_text(presenter)
    talk_title = ""

    quoted = re.findall(r'["“](.+?)["”]', raw_title)
    if quoted:
        talk_title = clean_text(quoted[-1])

    if not speaker:
        remainder = raw_title
        remainder = re.sub(re.escape(program), "", remainder, flags=re.I)
        remainder = re.sub(r"^\s*(Virtual|In Person)\s*-\s*", "", remainder, flags=re.I)
        remainder = re.sub(r'\s*-\s*["“].+?["”]\s*$', "", remainder)
        remainder = clean_text(remainder.strip(" -"))
        if remainder and remainder.lower() not in {"tbd"}:
            speaker = remainder

    return speaker, talk_title


def parse_event(page: Page, url: str, timezone_name: str) -> SeminarEvent:
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(300)

    soup = BeautifulSoup(page.content(), "html.parser")
    h1 = soup.find("h1")
    raw_title = clean_text(h1.get_text(" ", strip=True) if h1 else "")
    date_raw = get_label_value(soup, "Date")
    location = get_label_value(soup, "Location")
    presenter = get_label_value(soup, "Presenter")
    event_type = get_label_value(soup, "Event Type")
    description = get_label_value(soup, "Description")
    zoom_urls = extract_zoom_links(soup)

    if not raw_title or not date_raw:
        raise ValueError(f"Missing title or date: {url}")

    start, end = parse_date_range(date_raw, timezone_name)
    program = infer_program(raw_title, event_type)
    speaker, talk_title = split_title(raw_title, presenter, program)

    return SeminarEvent(
        source="winship",
        source_event_id=extract_event_id(url) or url,
        program=program,
        speaker=speaker,
        talk_title=talk_title,
        event_type=event_type,
        start=start,
        end=end,
        location=location,
        description=description,
        zoom_urls=zoom_urls,
        source_url=url,
        raw_title=raw_title,
    )


def fetch_winship_events(timezone_name: str) -> list[SeminarEvent]:
    events: list[SeminarEvent] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        urls = discover_urls(page)
        print(f"Winship: found {len(urls)} unique event links")

        for index, url in enumerate(urls, start=1):
            try:
                event = parse_event(page, url, timezone_name)
                events.append(event)
                print(
                    f"Winship [{index}/{len(urls)}] "
                    f"{event.start:%Y-%m-%d %H:%M} | {event.raw_title}"
                )
            except Exception as exc:
                print(f"Winship [{index}/{len(urls)}] FAILED {url}: {exc}")

        browser.close()

    return events
