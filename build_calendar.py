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
    fetch_human_genetics_events,
    fetch_winship_events,
)


def load_config() -> dict:
    with Path("config.yml").open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main() -> None:
    config = load_config()
    timezone_name = config["calendar"]["timezone"]

    fetchers: dict[str, Callable[[str], list[SeminarEvent]]] = {
        "winship": fetch_winship_events,
        "human_genetics": fetch_human_genetics_events,
        "bmi": fetch_bmi_events,
    }

    all_events: list[SeminarEvent] = []

    for source_name, fetcher in fetchers.items():
        source_config = config.get("sources", {}).get(source_name, {})
        if not source_config.get("enabled", False):
            print(f"{source_name}: disabled")
            continue

        try:
            all_events.extend(fetcher(timezone_name))
        except Exception as exc:
            # Keep other sources working if one page temporarily breaks.
            print(f"{source_name}: SOURCE FAILED: {exc}")

    included: list[SeminarEvent] = []
    excluded_reasons: Counter[str] = Counter()

    for event in all_events:
        include, reason = should_include_event(event, config, timezone_name)
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
