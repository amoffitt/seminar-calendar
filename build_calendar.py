#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Callable

import yaml

from calendar_writer import write_calendar
from filters import should_include_event
from google_sheets_output import write_google_sheet
from seminar_event import SeminarEvent
from slack_notifications import send_change_notification, send_weekly_digest
from sources import (
    fetch_bmi_events,
    fetch_cancer_genomics_events,
    fetch_gmb_events,
    fetch_human_genetics_events,
    fetch_winship_events,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weekly-digest", action="store_true")
    return parser.parse_args()


def load_config() -> dict:
    with Path("config.yml").open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    args = parse_args()
    config = load_config()
    timezone_name = config.get("calendar", {}).get(
        "timezone", "America/New_York"
    )
    sources_config = config.get("sources", {})

    fetchers: dict[str, Callable[[], list[SeminarEvent]]] = {
        "winship": lambda: fetch_winship_events(timezone_name),
        "human_genetics": lambda: fetch_human_genetics_events(timezone_name),
        "bmi": lambda: fetch_bmi_events(timezone_name),
        "gmb": lambda: fetch_gmb_events(
            timezone_name, sources_config["gmb"]["url"]
        ),
        "cancer_genomics": lambda: fetch_cancer_genomics_events(
            timezone_name, sources_config["cancer_genomics"]["url"]
        ),
    }

    all_events = []
    source_failures = []

    for source_name, fetcher in fetchers.items():
        if not sources_config.get(source_name, {}).get("enabled", False):
            continue
        try:
            all_events.extend(fetcher())
        except Exception as exc:
            failure = f"{source_name}: {exc}"
            source_failures.append(failure)
            print(f"{source_name}: SOURCE FAILED: {exc}")

    included_events = []
    excluded_reasons = Counter()

    for event in all_events:
        include, reason = should_include_event(event, config, timezone_name)
        if include:
            included_events.append(event)
        else:
            excluded_reasons[reason] += 1

    output_path = write_calendar(included_events, config)
    sheet_changes = write_google_sheet(included_events, config)

    slack_config = config.get("slack", {})
    sheet_url = slack_config.get("sheet_url", "")

    if slack_config.get("enabled", False):
        if args.weekly_digest:
            send_weekly_digest(
                included_events,
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

    print(f"Parsed {len(all_events)} total events")
    print(f"Included {len(included_events)} events")
    print(f"Wrote calendar to {output_path}")
    print(
        f"Changes: {sheet_changes['added']} added, "
        f"{sheet_changes['updated']} updated, "
        f"{sheet_changes['removed']} removed"
    )


if __name__ == "__main__":
    main()
