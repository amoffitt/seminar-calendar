#!/usr/bin/env python3

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

import yaml

from calendar_writer import write_calendar
from filters import should_include_event
from seminar_event import SeminarEvent
from sources import (
    fetch_bmi_events,
    fetch_cancer_genomics_events,
    fetch_gmb_events,
    fetch_human_genetics_events,
    fetch_winship_events,
)


def load_config() -> dict:
    with Path("config.yml").open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    config = load_config()
    timezone_name = config["calendar"]["timezone"]
    sources_config = config.get("sources", {})

    fetchers: dict[str, Callable[[], list[SeminarEvent]]] = {
        "winship": lambda: fetch_winship_events(timezone_name),
        "human_genetics": lambda: fetch_human_genetics_events(timezone_name),
        "bmi": lambda: fetch_bmi_events(timezone_name),
        "gmb": lambda: fetch_gmb_events(
            timezone_name,
            sources_config["gmb"]["url"],
        ),
        "cancer_genomics": lambda: fetch_cancer_genomics_events(
            timezone_name,
            sources_config["cancer_genomics"]["url"],
        ),
    }

    all_events: list[SeminarEvent] = []

    for source_name, fetcher in fetchers.items():
        source_config = sources_config.get(source_name, {})

        if not source_config.get("enabled", False):
            print(f"{source_name}: disabled")
            continue

        try:
            all_events.extend(fetcher())
        except Exception as exc:
            # A temporary failure in one source should not erase events
            # successfully gathered from the other sources.
            print(f"{source_name}: SOURCE FAILED: {exc}")

    included: list[SeminarEvent] = []
    excluded_reasons: Counter[str] = Counter()

    for event in all_events:
        include, reason = should_include_event(
            event,
            config,
            timezone_name,
        )

        if include:
            included.append(event)
        else:
            excluded_reasons[reason] += 1

    output = write_calendar(included, config)

    print()
    print(f"Parsed {len(all_events)} total events")
    print(f"Included {len(included)} events")
    print(f"Wrote calendar to {output}")

    if excluded_reasons:
        print("Excluded events:")
        for reason, count in excluded_reasons.most_common():
            print(f"  {count:3d}  {reason}")

    by_source = Counter(event.source for event in included)
    print("Included by source:")
    for source, count in sorted(by_source.items()):
        print(f"  {source}: {count}")


if __name__ == "__main__":
    main()
