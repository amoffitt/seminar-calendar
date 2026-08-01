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


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

EVENT_HEADERS = [
    "Date",
    "Day",
    "Start Time",
    "End Time",
    "Program",
    "Speaker",
    "Talk Title",
    "Source",
    "Location",
    "Zoom URL",
    "Priority",
    "Notes",
    "Event Type",
    "Affiliation",
    "Host",
    "Source URL",
    "Event ID",
    "Last Updated",
]

PROGRAM_HEADERS = [
    "Source",
    "Program",
    "Included Events",
]

CHANGE_HEADERS = [
    "Detected At",
    "Action",
    "Event ID",
    "Source",
    "Program",
    "Speaker",
    "Talk Title",
    "Date",
]


def authorize_gspread() -> gspread.Client:
    raw = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )

    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is missing"
        )

    info = json.loads(raw)
    credentials = Credentials.from_service_account_info(
        info,
        scopes=SCOPES,
    )

    return gspread.authorize(credentials)


def get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    rows: int,
    cols: int,
) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=title,
            rows=rows,
            cols=cols,
        )


def read_existing_rows(
    worksheet: gspread.Worksheet,
) -> dict[str, dict[str, str]]:
    values = worksheet.get_all_values()

    if not values:
        return {}

    headers = values[0]
    existing: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        padded = row + [""] * (
            len(headers) - len(row)
        )
        record = dict(
            zip(
                headers,
                padded,
            )
        )

        event_id = record.get(
            "Event ID",
            "",
        ).strip()

        if event_id:
            existing[event_id] = record

    return existing


def merge_existing_rows(
    *collections: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}

    for collection in collections:
        merged.update(collection)

    return merged


def event_row(
    event: SeminarEvent,
    previous: dict[str, str] | None,
    updated_at: str,
) -> list[str]:
    previous = previous or {}

    return [
        event.start.strftime("%Y-%m-%d"),
        event.start.strftime("%a"),
        event.start.strftime("%-I:%M %p"),
        event.end.strftime("%-I:%M %p"),
        event.program,
        event.speaker,
        event.talk_title,
        event.source,
        event.location,
        (
            event.zoom_urls[0]
            if event.zoom_urls
            else ""
        ),
        previous.get("Priority", ""),
        previous.get("Notes", ""),
        event.event_type,
        event.affiliation,
        event.host,
        event.source_url,
        event.uid,
        updated_at,
    ]


def comparable_event_values(
    event: SeminarEvent,
) -> dict[str, str]:
    return {
        "Date": event.start.strftime("%Y-%m-%d"),
        "Day": event.start.strftime("%a"),
        "Start Time": event.start.strftime("%-I:%M %p"),
        "End Time": event.end.strftime("%-I:%M %p"),
        "Program": event.program,
        "Speaker": event.speaker,
        "Talk Title": event.talk_title,
        "Source": event.source,
        "Location": event.location,
        "Zoom URL": (
            event.zoom_urls[0]
            if event.zoom_urls
            else ""
        ),
        "Event Type": event.event_type,
        "Affiliation": event.affiliation,
        "Host": event.host,
        "Source URL": event.source_url,
    }


def detect_changes(
    current_events: list[SeminarEvent],
    past_events: list[SeminarEvent],
    previous_current: dict[str, dict[str, str]],
    previous_past: dict[str, dict[str, str]],
) -> dict[str, Any]:
    all_events = current_events + past_events
    current_by_id = {
        event.uid: event
        for event in current_events
    }
    all_by_id = {
        event.uid: event
        for event in all_events
    }
    previous_all = merge_existing_rows(
        previous_past,
        previous_current,
    )

    added_events: list[SeminarEvent] = []
    updated_events: list[SeminarEvent] = []
    removed_events: list[dict[str, str]] = []
    archived_events: list[SeminarEvent] = []

    for event_id, event in all_by_id.items():
        previous = previous_all.get(event_id)

        if previous is None:
            added_events.append(event)
            continue

        current_values = comparable_event_values(
            event
        )

        if any(
            current_values[field]
            != previous.get(field, "")
            for field in current_values
        ):
            updated_events.append(event)

    for event_id, previous in previous_all.items():
        if event_id not in all_by_id:
            removed_events.append(previous)

    previous_current_ids = set(
        previous_current
    )
    current_ids = set(
        current_by_id
    )
    past_by_id = {
        event.uid: event
        for event in past_events
    }

    newly_archived_ids = (
        previous_current_ids
        - current_ids
    ) & set(past_by_id)

    archived_events = [
        past_by_id[event_id]
        for event_id in newly_archived_ids
    ]

    return {
        "added": len(added_events),
        "updated": len(updated_events),
        "removed": len(removed_events),
        "archived": len(archived_events),
        "added_events": added_events,
        "updated_events": updated_events,
        "removed_events": removed_events,
        "archived_events": archived_events,
    }


def write_event_sheet(
    worksheet: gspread.Worksheet,
    events: list[SeminarEvent],
    existing_all: dict[str, dict[str, str]],
    updated_at: str,
    *,
    newest_first: bool,
) -> None:
    sorted_events = sorted(
        events,
        key=lambda event: (
            event.start,
            event.source,
            event.program,
            event.speaker,
        ),
        reverse=newest_first,
    )

    rows = [
        event_row(
            event,
            existing_all.get(event.uid),
            updated_at,
        )
        for event in sorted_events
    ]

    worksheet.clear()
    worksheet.update(
        [EVENT_HEADERS] + rows,
        value_input_option="USER_ENTERED",
    )
    worksheet.freeze(rows=1)

    if rows:
        worksheet.set_basic_filter(
            f"A1:R{len(rows) + 1}"
        )


def append_change_history(
    worksheet: gspread.Worksheet,
    changes: dict[str, Any],
    detected_at: str,
) -> None:
    rows: list[list[str]] = []

    for action, key in [
        ("ADDED", "added_events"),
        ("UPDATED", "updated_events"),
        ("ARCHIVED", "archived_events"),
    ]:
        for event in changes[key]:
            rows.append(
                [
                    detected_at,
                    action,
                    event.uid,
                    event.source,
                    event.program,
                    event.speaker,
                    event.talk_title,
                    event.start.strftime(
                        "%Y-%m-%d"
                    ),
                ]
            )

    for record in changes["removed_events"]:
        rows.append(
            [
                detected_at,
                "REMOVED",
                record.get("Event ID", ""),
                record.get("Source", ""),
                record.get("Program", ""),
                record.get("Speaker", ""),
                record.get("Talk Title", ""),
                record.get("Date", ""),
            ]
        )

    if not rows:
        return

    if not worksheet.get_all_values():
        worksheet.update(
            [CHANGE_HEADERS],
            value_input_option="USER_ENTERED",
        )

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )
    worksheet.freeze(rows=1)


def write_google_sheet(
    current_events: list[SeminarEvent],
    past_events: list[SeminarEvent],
    config: dict[str, Any],
) -> dict[str, Any]:
    output = config.get(
        "google_output",
        {},
    )

    empty_result = {
        "added": 0,
        "updated": 0,
        "removed": 0,
        "archived": 0,
        "added_events": [],
        "updated_events": [],
        "removed_events": [],
        "archived_events": [],
    }

    if not output.get("enabled", False):
        print("Google Sheets output: disabled")
        return empty_result

    timezone_name = config["calendar"]["timezone"]
    now = datetime.now(
        ZoneInfo(timezone_name)
    )
    updated_at = now.strftime(
        "%Y-%m-%d %I:%M %p %Z"
    )
    detected_at = now.isoformat(
        timespec="seconds"
    )

    client = authorize_gspread()
    spreadsheet = client.open_by_key(
        output["spreadsheet_id"]
    )

    current_ws = get_or_create_worksheet(
        spreadsheet,
        output.get(
            "current_events_tab",
            "Current Events",
        ),
        max(
            len(current_events) + 500,
            1000,
        ),
        len(EVENT_HEADERS),
    )

    past_ws = get_or_create_worksheet(
        spreadsheet,
        output.get(
            "past_events_tab",
            "Past Seminars",
        ),
        max(
            len(past_events) + 500,
            1000,
        ),
        len(EVENT_HEADERS),
    )

    programs_ws = get_or_create_worksheet(
        spreadsheet,
        output.get(
            "programs_tab",
            "Programs",
        ),
        500,
        len(PROGRAM_HEADERS),
    )

    changes_ws = get_or_create_worksheet(
        spreadsheet,
        output.get(
            "changes_tab",
            "Changes",
        ),
        5000,
        len(CHANGE_HEADERS),
    )

    previous_current = read_existing_rows(
        current_ws
    )
    previous_past = read_existing_rows(
        past_ws
    )
    previous_all = merge_existing_rows(
        previous_past,
        previous_current,
    )

    changes = detect_changes(
        current_events,
        past_events,
        previous_current,
        previous_past,
    )

    write_event_sheet(
        current_ws,
        current_events,
        previous_all,
        updated_at,
        newest_first=False,
    )

    write_event_sheet(
        past_ws,
        past_events,
        previous_all,
        updated_at,
        newest_first=True,
    )

    program_counts = Counter(
        (
            event.source,
            event.program,
        )
        for event in current_events
    )

    program_rows = [
        [
            source,
            program,
            count,
        ]
        for (
            source,
            program,
        ), count in sorted(
            program_counts.items()
        )
    ]

    programs_ws.clear()
    programs_ws.update(
        [PROGRAM_HEADERS] + program_rows,
        value_input_option="USER_ENTERED",
    )
    programs_ws.freeze(rows=1)

    append_change_history(
        changes_ws,
        changes,
        detected_at,
    )

    print(
        "Google Sheets output: "
        f"{len(current_events)} current events; "
        f"{len(past_events)} past events; "
        f"{changes['added']} added, "
        f"{changes['updated']} updated, "
        f"{changes['removed']} removed, "
        f"{changes['archived']} newly archived"
    )

    return changes
