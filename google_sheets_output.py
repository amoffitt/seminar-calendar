from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from seminar_event import SeminarEvent


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CURRENT_HEADERS = [
    "Date",
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

MANUAL_COLUMNS = {
    "Priority",
    "Notes",
}


def authorize_gspread() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is missing"
        )

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON"
        ) from exc

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


def normalize_existing_rows(
    worksheet: gspread.Worksheet,
) -> dict[str, dict[str, str]]:
    values = worksheet.get_all_values()

    if not values:
        return {}

    headers = values[0]
    existing: dict[str, dict[str, str]] = {}

    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, padded))
        event_id = record.get("Event ID", "").strip()

        if event_id:
            existing[event_id] = record

    return existing


def event_row(
    event: SeminarEvent,
    previous: dict[str, str] | None,
    updated_at: str,
) -> list[str]:
    previous = previous or {}

    return [
        event.start.strftime("%Y-%m-%d"),
        event.start.strftime("%-I:%M %p"),
        event.end.strftime("%-I:%M %p"),
        event.program,
        event.speaker,
        event.talk_title,
        event.source,
        event.location,
        event.zoom_urls[0] if event.zoom_urls else "",
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
        "Start Time": event.start.strftime("%-I:%M %p"),
        "End Time": event.end.strftime("%-I:%M %p"),
        "Program": event.program,
        "Speaker": event.speaker,
        "Talk Title": event.talk_title,
        "Source": event.source,
        "Location": event.location,
        "Zoom URL": event.zoom_urls[0] if event.zoom_urls else "",
        "Event Type": event.event_type,
        "Affiliation": event.affiliation,
        "Host": event.host,
        "Source URL": event.source_url,
    }


def change_row(
    event: SeminarEvent,
    action: str,
    detected_at: str,
) -> list[str]:
    return [
        detected_at,
        action,
        event.uid,
        event.source,
        event.program,
        event.speaker,
        event.talk_title,
        event.start.strftime("%Y-%m-%d"),
    ]


def detect_changes(
    events: list[SeminarEvent],
    existing: dict[str, dict[str, str]],
    detected_at: str,
) -> list[list[str]]:
    current = {
        event.uid: event
        for event in events
    }

    changes: list[list[str]] = []

    for event_id, event in current.items():
        previous = existing.get(event_id)

        if previous is None:
            changes.append(
                change_row(
                    event,
                    "ADDED",
                    detected_at,
                )
            )
            continue

        current_values = comparable_event_values(event)

        if any(
            current_values[field]
            != previous.get(field, "")
            for field in current_values
        ):
            changes.append(
                change_row(
                    event,
                    "UPDATED",
                    detected_at,
                )
            )

    for event_id, previous in existing.items():
        if event_id in current:
            continue

        changes.append(
            [
                detected_at,
                "REMOVED",
                event_id,
                previous.get("Source", ""),
                previous.get("Program", ""),
                previous.get("Speaker", ""),
                previous.get("Talk Title", ""),
                previous.get("Date", ""),
            ]
        )

    return changes


def rgb(
    red: int,
    green: int,
    blue: int,
) -> dict[str, float]:
    return {
        "red": red / 255,
        "green": green / 255,
        "blue": blue / 255,
    }


def format_current_events_sheet(
    spreadsheet: gspread.Spreadsheet,
    worksheet: gspread.Worksheet,
    row_count: int,
    timezone_name: str,
) -> None:
    sheet_id = worksheet.id
    today = datetime.now(
        ZoneInfo(timezone_name)
    ).date()

    upcoming_end = today + timedelta(days=7)

    header_format = {
        "backgroundColor": rgb(44, 95, 45),
        "textFormat": {
            "foregroundColor": rgb(255, 255, 255),
            "bold": True,
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    }

    requests: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "frozenRowCount": 1,
                    },
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(CURRENT_HEADERS),
                },
                "cell": {
                    "userEnteredFormat": header_format,
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": max(row_count, 1),
                        "startColumnIndex": 0,
                        "endColumnIndex": len(CURRENT_HEADERS),
                    }
                }
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(row_count, 2),
                    "startColumnIndex": 3,
                    "endColumnIndex": 8,
                },
                "cell": {
                    "userEnteredFormat": {
                        "wrapStrategy": "WRAP",
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": (
                    "userEnteredFormat.wrapStrategy,"
                    "userEnteredFormat.verticalAlignment"
                ),
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(row_count, 2),
                    "startColumnIndex": 9,
                    "endColumnIndex": 11,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": rgb(255, 249, 196),
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.wrapStrategy"
                ),
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(row_count + 500, 1000),
                    "startColumnIndex": 9,
                    "endColumnIndex": 10,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "High"},
                            {"userEnteredValue": "Medium"},
                            {"userEnteredValue": "Low"},
                        ],
                    },
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(CURRENT_HEADERS),
                }
            }
        },
        {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [
                        {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": max(row_count, 2),
                            "startColumnIndex": 0,
                            "endColumnIndex": len(CURRENT_HEADERS),
                        }
                    ],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [
                                {
                                    "userEnteredValue": (
                                        '=AND($A2>=TODAY(),$A2<=TODAY()+7)'
                                    )
                                }
                            ],
                        },
                        "format": {
                            "backgroundColor": rgb(226, 239, 218),
                        },
                    },
                },
                "index": 0,
            }
        },
    ]

    spreadsheet.batch_update(
        {
            "requests": requests,
        }
    )

    # Apply practical fixed widths after auto-resize.
    width_requests = []

    widths = {
        0: 95,    # Date
        1: 85,    # Start
        2: 85,    # End
        3: 220,   # Program
        4: 190,   # Speaker
        5: 320,   # Talk title
        6: 110,   # Source
        7: 220,   # Location
        8: 180,   # Zoom URL
        9: 90,    # Priority
        10: 240,  # Notes
        11: 120,  # Event type
        12: 220,  # Affiliation
        13: 160,  # Host
        14: 220,  # Source URL
        15: 190,  # Event ID
        16: 160,  # Last updated
    }

    for column_index, width in widths.items():
        width_requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": column_index,
                        "endIndex": column_index + 1,
                    },
                    "properties": {
                        "pixelSize": width,
                    },
                    "fields": "pixelSize",
                }
            }
        )

    spreadsheet.batch_update(
        {
            "requests": width_requests,
        }
    )


def write_google_sheet(
    events: list[SeminarEvent],
    config: dict[str, Any],
) -> dict[str, int]:
    output_config = config.get(
        "google_output",
        {},
    )

    if not output_config.get("enabled", False):
        print("Google Sheets output: disabled")
        return {
            "added": 0,
            "updated": 0,
            "removed": 0,
        }

    timezone_name = config["calendar"]["timezone"]
    local_tz = ZoneInfo(timezone_name)

    now = datetime.now(local_tz)
    updated_at = now.strftime(
        "%Y-%m-%d %I:%M %p %Z"
    )
    detected_at = now.isoformat(
        timespec="seconds"
    )

    client = authorize_gspread()
    spreadsheet = client.open_by_key(
        output_config["spreadsheet_id"]
    )

    current_ws = get_or_create_worksheet(
        spreadsheet,
        output_config.get(
            "current_events_tab",
            "Current Events",
        ),
        rows=max(
            len(events) + 500,
            1000,
        ),
        cols=len(CURRENT_HEADERS),
    )

    programs_ws = get_or_create_worksheet(
        spreadsheet,
        output_config.get(
            "programs_tab",
            "Programs",
        ),
        rows=500,
        cols=len(PROGRAM_HEADERS),
    )

    changes_ws = get_or_create_worksheet(
        spreadsheet,
        output_config.get(
            "changes_tab",
            "Changes",
        ),
        rows=5000,
        cols=len(CHANGE_HEADERS),
    )

    existing = normalize_existing_rows(
        current_ws
    )

    changes = detect_changes(
        events,
        existing,
        detected_at,
    )

    sorted_events = sorted(
        events,
        key=lambda event: (
            event.start,
            event.source,
            event.program,
            event.speaker,
        ),
    )

    current_rows = [
        event_row(
            event,
            existing.get(event.uid),
            updated_at,
        )
        for event in sorted_events
    ]

    current_ws.clear()
    current_ws.update(
        [CURRENT_HEADERS] + current_rows,
        value_input_option="USER_ENTERED",
    )

    format_current_events_sheet(
        spreadsheet,
        current_ws,
        len(current_rows) + 1,
        timezone_name,
    )

    program_counts = Counter(
        (event.source, event.program)
        for event in events
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

    if changes:
        if not changes_ws.get_all_values():
            changes_ws.update(
                [CHANGE_HEADERS],
                value_input_option="USER_ENTERED",
            )

        changes_ws.append_rows(
            changes,
            value_input_option="USER_ENTERED",
        )
        changes_ws.freeze(rows=1)


    counts = Counter(
    row[1]
    for row in changes
    )

    events_by_id = {
        event.uid: event
        for event in events
    }

    added_events = [
        events_by_id[row[2]]
        for row in changes
        if row[1] == "ADDED" and row[2] in events_by_id
    ]

    updated_events = [
        events_by_id[row[2]]
        for row in changes
        if row[1] == "UPDATED" and row[2] in events_by_id
    ]

    removed_events = [
        existing[row[2]]
        for row in changes
        if row[1] == "REMOVED" and row[2] in existing
    ]

    result = {
        "added": counts.get("ADDED", 0),
        "updated": counts.get("UPDATED", 0),
        "removed": counts.get("REMOVED", 0),
        "added_events": added_events,
        "updated_events": updated_events,
        "removed_events": removed_events,
    }

    print(
        "Google Sheets output: "
        f"{len(events)} current events; "
        f"{result['added']} added, "
        f"{result['updated']} updated, "
        f"{result['removed']} removed"
    )

    return result
