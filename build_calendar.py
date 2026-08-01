#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import yaml

from calendar_writer import write_calendar
from filters import should_include_event
from google_sheets_output import write_google_sheet
from seminar_event import SeminarEvent
from slack_notifications import (
    send_change_notification,
    send_weekly_digest,
)
from sources import (
    fetch_bmi_events,
    fetch_cancer_genomics_events,
    fetch_gmb_events,
    fetch_human_genetics_events,
    fetch_leukemia_research_events,
    fetch_winship_events,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--weekly-digest",
        action="store_true",
    )
    return parser.parse_args()


def load_config() -> dict:
    with Path("config.yml").open(
        "r",
        encoding="utf-8",
    ) as handle:
        return yaml.safe_load(handle)


def main() -> None:
    args = parse_args()
    config = load_config()

    timezone_name = config.get(
        "calendar",
        {},
    ).get(
        "timezone",
        "America/New_York",
    )
    sources_config = config.get("sources", {})

    fetchers: dict[str, Callable[[], list[SeminarEvent]]] = {
        "winship": lambda: fetch_winship_events(
            timezone_name
        ),
        "human_genetics": lambda: fetch_human_genetics_events(
            timezone_name
        ),
        "bmi": lambda: fetch_bmi_events(
            timezone_name
        ),
        "gmb": lambda: fetch_gmb_events(
            timezone_name,
            sources_config["gmb"]["url"],
        ),
        "cancer_genomics": lambda: fetch_cancer_genomics_events(
            timezone_name,
            sources_config["cancer_genomics"]["url"],
        ),
        "leukemia_research": lambda: fetch_leukemia_research_events(
            timezone_name,
            sources_config["leukemia_research"]["url"],
        ),
    }

    all_events: list[SeminarEvent] = []
    source_failures: list[str] = []

    for source_name, fetcher in fetchers.items():
        source_config = sources_config.get(
            source_name,
            {},
        )

        if not source_config.get("enabled", False):
            print(f"{source_name}: disabled")
            continue

        try:
            all_events.extend(fetcher())
        except Exception as exc:
            failure = f"{source_name}: {exc}"
            source_failures.append(failure)
            print(
                f"{source_name}: SOURCE FAILED: {exc}"
            )

    filtered_events: list[SeminarEvent] = []
    excluded_reasons: Counter[str] = Counter()

    # Keep past events at this stage. They are needed for the archive.
    for event in all_events:
        include, reason = should_include_event(
            event,
            config,
            timezone_name,
            apply_date_filter=False,
        )

        if include:
            filtered_events.append(event)
        else:
            excluded_reasons[reason] += 1

    now = datetime.now(
        ZoneInfo(timezone_name)
    )

    current_events = [
        event
        for event in filtered_events
        if event.end >= now
    ]

    past_events = [
        event
        for event in filtered_events
        if event.end < now
    ]

    # Outlook and Slack use only current/upcoming events.
    output_path = write_calendar(
        current_events,
        config,
    )

    # Google Sheets receives both current and archived events.
    sheet_changes = write_google_sheet(
        current_events,
        past_events,
        config,
    )

    slack_config = config.get("slack", {})
    sheet_url = slack_config.get(
        "sheet_url",
        "",
    )

    if slack_config.get("enabled", False):
        if args.weekly_digest:
            send_weekly_digest(
                current_events,
                timezone_name,
                sheet_url,
                source_failures,
            )
        else:
            send_change_notification(
                sheet_changes,
                source_failures,
                sheet_url,
            )
    else:
        print("Slack notifications: disabled")

    print()
    print(f"Parsed {len(all_events)} total events")
    print(
        f"Included {len(filtered_events)} events after content filters"
    )
    print(f"Current events: {len(current_events)}")
    print(f"Past events: {len(past_events)}")
    print(f"Wrote calendar to {output_path}")

    if excluded_reasons:
        print("Excluded events:")
        for reason, count in excluded_reasons.most_common():
            print(f"  {count:3d}  {reason}")

    by_source = Counter(
        event.source
        for event in current_events
    )
    print("Current events by source:")
    for source_name, count in sorted(
        by_source.items()
    ):
        print(f"  {source_name}: {count}")

    print(
        "Changes: "
        f"{sheet_changes['added']} added, "
        f"{sheet_changes['updated']} updated, "
        f"{sheet_changes['removed']} removed, "
        f"{sheet_changes['archived']} newly archived"
    )


if __name__ == "__main__":
    main()
