from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from icalendar import Calendar, Event

from seminar_event import SeminarEvent


def write_calendar(
    events: list[SeminarEvent],
    config: dict[str, Any],
) -> Path:
    calendar_config = config["calendar"]
    output_path = Path(calendar_config["output"])
    title_template = calendar_config["title_format"]

    cal = Calendar()
    cal.add("prodid", "-//Emory Seminar Aggregator//github.com/amoffitt//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", calendar_config["name"])

    now = datetime.now(UTC)

    for item in sorted(events, key=lambda event: (event.start, event.source, event.uid)):
        component = Event()
        component.add("uid", item.uid)
        component.add("summary", item.compact_summary(title_template))
        component.add("dtstart", item.start)
        component.add("dtend", item.end)
        component.add("dtstamp", now)
        component.add("status", "CONFIRMED")
        component.add("transp", "OPAQUE")
        component.add("description", item.description_text())
        component.add("url", item.source_url)

        if item.location:
            component.add("location", item.location)
        elif item.zoom_urls:
            component.add("location", "Zoom")

        cal.add_component(component)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(cal.to_ical())
    return output_path
