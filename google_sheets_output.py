from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from seminar_event import SeminarEvent


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CURRENT_HEADERS = [
    "Event ID", "Source", "Program", "Speaker", "Talk Title",
    "Date", "Start Time", "End Time", "Location", "Zoom URL",
    "Event Type", "Affiliation", "Host", "Source URL", "Last Updated",
]

PROGRAM_HEADERS = ["Source", "Program", "Included Events"]

CHANGE_HEADERS = [
    "Detected At", "Action", "Event ID", "Source",
    "Program", "Speaker", "Talk Title", "Date",
]


def authorize_gspread() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is missing")
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create(spreadsheet, title: str, rows: int, cols: int):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def event_row(event: SeminarEvent, updated_at: str) -> list[str]:
    return [
        event.uid,
        event.source,
        event.program,
        event.speaker,
        event.talk_title,
        event.start.strftime("%Y-%m-%d"),
        event.start.strftime("%-I:%M %p"),
        event.end.strftime("%-I:%M %p"),
        event.location,
        event.zoom_urls[0] if event.zoom_urls else "",
        event.event_type,
        event.affiliation,
        event.host,
        event.source_url,
        updated_at,
    ]


def comparable(event: SeminarEvent) -> dict[str, str]:
    return {
        "Source": event.source,
        "Program": event.program,
        "Speaker": event.speaker,
        "Talk Title": event.talk_title,
        "Date": event.start.strftime("%Y-%m-%d"),
        "Start Time": event.start.strftime("%-I:%M %p"),
        "End Time": event.end.strftime("%-I:%M %p"),
        "Location": event.location,
        "Zoom URL": event.zoom_urls[0] if event.zoom_urls else "",
        "Event Type": event.event_type,
        "Affiliation": event.affiliation,
        "Host": event.host,
        "Source URL": event.source_url,
    }


def read_existing(ws) -> dict[str, dict[str, str]]:
    values = ws.get_all_values()
    if not values or values[0] != CURRENT_HEADERS:
        return {}

    existing = {}
    for row in values[1:]:
        padded = row + [""] * (len(CURRENT_HEADERS) - len(row))
        record = dict(zip(CURRENT_HEADERS, padded))
        event_id = record["Event ID"].strip()
        if event_id:
            existing[event_id] = record
    return existing


def detect_changes(
    events: list[SeminarEvent],
    existing: dict[str, dict[str, str]],
    detected_at: str,
) -> list[list[str]]:
    current = {event.uid: event for event in events}
    rows: list[list[str]] = []

    for event_id, event in current.items():
        previous = existing.get(event_id)
        action = None

        if previous is None:
            action = "ADDED"
        else:
            now_values = comparable(event)
            if any(
                now_values[field] != previous.get(field, "")
                for field in now_values
            ):
                action = "UPDATED"

        if action:
            rows.append([
                detected_at,
                action,
                event.uid,
                event.source,
                event.program,
                event.speaker,
                event.talk_title,
                event.start.strftime("%Y-%m-%d"),
            ])

    for event_id, previous in existing.items():
        if event_id not in current:
            rows.append([
                detected_at,
                "REMOVED",
                event_id,
                previous.get("Source", ""),
                previous.get("Program", ""),
                previous.get("Speaker", ""),
                previous.get("Talk Title", ""),
                previous.get("Date", ""),
            ])

    return rows


def write_google_sheet(
    events: list[SeminarEvent],
    config: dict[str, Any],
) -> dict[str, int]:
    output = config.get("google_output", {})
    if not output.get("enabled", False):
        print("Google Sheets output: disabled")
        return {"added": 0, "updated": 0, "removed": 0}

    tz = ZoneInfo(config["calendar"]["timezone"])
    now = datetime.now(tz)
    updated_at = now.strftime("%Y-%m-%d %I:%M %p %Z")
    detected_at = now.isoformat(timespec="seconds")

    client = authorize_gspread()
    spreadsheet = client.open_by_key(output["spreadsheet_id"])

    current_ws = get_or_create(
        spreadsheet,
        output.get("current_events_tab", "Current Events"),
        max(len(events) + 100, 1000),
        len(CURRENT_HEADERS),
    )
    programs_ws = get_or_create(
        spreadsheet,
        output.get("programs_tab", "Programs"),
        500,
        len(PROGRAM_HEADERS),
    )
    changes_ws = get_or_create(
        spreadsheet,
        output.get("changes_tab", "Changes"),
        5000,
        len(CHANGE_HEADERS),
    )

    existing = read_existing(current_ws)
    changes = detect_changes(events, existing, detected_at)

    current_rows = [
        event_row(event, updated_at)
        for event in sorted(
            events,
            key=lambda e: (e.start, e.source, e.program, e.speaker),
        )
    ]
    current_ws.clear()
    current_ws.update([CURRENT_HEADERS] + current_rows)
    current_ws.freeze(rows=1)

    program_counts = Counter((e.source, e.program) for e in events)
    program_rows = [
        [source, program, count]
        for (source, program), count in sorted(program_counts.items())
    ]
    programs_ws.clear()
    programs_ws.update([PROGRAM_HEADERS] + program_rows)
    programs_ws.freeze(rows=1)

    if changes:
        existing_values = changes_ws.get_all_values()
        if not existing_values:
            changes_ws.update([CHANGE_HEADERS])
        changes_ws.append_rows(changes)
        changes_ws.freeze(rows=1)

    counts = Counter(row[1] for row in changes)
    result = {
        "added": counts.get("ADDED", 0),
        "updated": counts.get("UPDATED", 0),
        "removed": counts.get("REMOVED", 0),
    }

    print(
        "Google Sheets output: "
        f"{len(events)} current events; "
        f"{result['added']} added, "
        f"{result['updated']} updated, "
        f"{result['removed']} removed"
    )
    return result
