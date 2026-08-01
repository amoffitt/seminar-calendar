from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from seminar_event import SeminarEvent


def matches_any(value: str, patterns: list[str]) -> bool:
    return any(
        re.search(pattern, value or "", re.IGNORECASE)
        for pattern in patterns
    )


def should_include_event(
    event: SeminarEvent,
    config: dict[str, Any],
    timezone_name: str,
    *,
    apply_date_filter: bool = True,
) -> tuple[bool, str]:
    """
    Apply source, program, event-type, and title filters.

    When apply_date_filter=False, past events are retained so they can be
    written to the Past Seminars archive. Calendar generation can then
    separately select only current/upcoming events.
    """
    filters = config.get("filters", {})
    source_config = config.get("sources", {}).get(event.source, {})

    if apply_date_filter:
        local_tz = ZoneInfo(timezone_name)
        now = datetime.now(local_tz)
        past_days = int(filters.get("include_past_days", 0))
        cutoff = now - timedelta(days=past_days)

        if event.end < cutoff:
            return False, "past event"

    combined_title = " | ".join(
        part
        for part in [
            event.raw_title,
            event.program,
            event.speaker,
            event.talk_title,
        ]
        if part
    )

    global_excludes = filters.get(
        "global_exclude_title_patterns",
        [],
    )
    if matches_any(combined_title, global_excludes):
        return False, "global title exclusion"

    source_title_excludes = source_config.get(
        "exclude_title_patterns",
        [],
    )
    if matches_any(combined_title, source_title_excludes):
        return False, f"{event.source} title exclusion"

    event_type_excludes = source_config.get(
        "exclude_event_type_patterns",
        [],
    )
    if matches_any(event.event_type, event_type_excludes):
        return False, f"{event.source} event-type exclusion"

    include_programs = source_config.get(
        "include_program_patterns",
        [],
    )
    if include_programs and not matches_any(
        event.program,
        include_programs,
    ):
        return False, f"{event.source} program not included"

    if event.source == "human_genetics":
        include_sections = source_config.get(
            "include_sections",
            [],
        )
        exclude_sections = source_config.get(
            "exclude_sections",
            [],
        )

        if include_sections and event.program not in include_sections:
            return False, "Human Genetics section not included"

        if event.program in exclude_sections:
            return False, "Human Genetics section excluded"

        if source_config.get("exclude_tbd_speakers", False):
            if (
                not event.speaker
                or event.speaker.strip().upper() == "TBD"
            ):
                return False, "TBD Human Genetics speaker"

    return True, "included"
