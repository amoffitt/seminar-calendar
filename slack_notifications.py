from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from seminar_event import SeminarEvent


def _post(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL is missing")

    response = requests.post(
        url,
        json={"text": text, "unfurl_links": False, "unfurl_media": False},
        timeout=30,
    )
    response.raise_for_status()


def _event_lines(event: SeminarEvent, include_day: bool = True) -> list[str]:
    if include_day:
        heading = f"{event.start:%a %b %-d · %-I:%M %p}"
    else:
        heading = f"{event.start:%-I:%M %p}"

    lines = [f"• *{heading}* — *{event.program or event.source}*"]

    if event.speaker:
        lines.append(f"  {event.speaker}")
    if event.talk_title:
        lines.append(f"  _{event.talk_title}_")

    access = []
    if event.zoom_urls:
        access.append(f"<{event.zoom_urls[0]}|Join Zoom>")
    if event.location and event.location.lower() not in {"zoom", "virtual"}:
        access.append(event.location)
    if access:
        lines.append("  " + " · ".join(access))

    return lines


def send_change_notification(
    changes: dict,
    source_failures: list[str],
    sheet_url: str,
) -> None:
    added = changes.get("added_events", [])
    updated = changes.get("updated_events", [])
    removed = changes.get("removed_events", [])

    if not added and not updated and not removed and not source_failures:
        print("Slack: no changes or failures; no message sent")
        return

    lines = ["*📅 Emory Seminar Calendar Updated*"]

    for heading, events in [("Added", added), ("Updated", updated)]:
        if events:
            lines += ["", f"*{heading} ({len(events)})*"]
            for event in sorted(events, key=lambda e: e.start)[:12]:
                lines += _event_lines(event)
            if len(events) > 12:
                lines.append(f"  …and {len(events) - 12} more in the seminar database")

    if removed:
        lines += ["", f"*Removed ({len(removed)})*"]
        for record in removed[:12]:
            date_time = " · ".join(
                value for value in [
                    record.get("Date", ""),
                    record.get("Start Time", ""),
                ]
                if value
            )
            program = record.get("Program", "") or record.get("Source", "")
            lines.append(f"• *{date_time}* — *{program}*")
            if record.get("Speaker"):
                lines.append(f"  {record['Speaker']}")
            if record.get("Talk Title"):
                lines.append(f"  _{record['Talk Title']}_")

    if source_failures:
        lines += ["", f"*⚠️ Source failures ({len(source_failures)})*"]
        lines += [f"• {failure}" for failure in source_failures]

    lines += ["", f"<{sheet_url}|Open the seminar database>"]
    _post("\n".join(lines))
    print("Slack: change/failure notification sent")


def send_weekly_digest(
    events: list[SeminarEvent],
    timezone_name: str,
    sheet_url: str,
    source_failures: list[str] | None = None,
) -> None:
    source_failures = source_failures or []
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)

    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_monday = monday + timedelta(days=7)

    week_events = sorted(
        [
            event for event in events
            if monday <= event.start.astimezone(tz) < next_monday
        ],
        key=lambda event: event.start,
    )


    sunday = next_monday - timedelta(days=1)

    if monday.month == sunday.month:
        week_label = (
            f"Week of {monday:%B %-d}–{sunday:%-d, %Y}"
        )
    else:
        week_label = (
            f"Week of {monday:%B %-d}–{sunday:%B %-d, %Y}"
        )

    lines = [
        "*📅 Emory Seminar Digest*",
        week_label,
    ]


    if not week_events:
        lines += ["", "No included seminars are currently scheduled for this week."]
    else:
        grouped = defaultdict(list)
        for event in week_events:
            grouped[event.start.strftime("%A, %B %-d")].append(event)

        for day, day_events in grouped.items():
            lines += ["", f"*{day}*"]
            for event in day_events:
                lines += _event_lines(event, include_day=False)

    if source_failures:
        lines += ["", "*⚠️ Source failures during this update*"]
        lines += [f"• {failure}" for failure in source_failures]

    lines += ["", f"<{sheet_url}|Open the seminar database>"]
    _post("\n".join(lines))
    print(f"Slack: weekly digest sent with {len(week_events)} events")
