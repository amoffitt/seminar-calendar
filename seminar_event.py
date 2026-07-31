from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


@dataclass
class SeminarEvent:
    source: str
    source_event_id: str
    program: str
    start: datetime
    end: datetime
    source_url: str

    speaker: str = ""
    talk_title: str = ""
    event_type: str = ""
    affiliation: str = ""
    host: str = ""
    location: str = ""
    description: str = ""
    zoom_urls: list[str] = field(default_factory=list)
    raw_title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        stable = f"{self.source}:{self.source_event_id}"
        digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]
        return f"{self.source}-{digest}@emory-seminars"

    def compact_summary(self, template: str) -> str:
        values = {
            "program": clean_text(self.program),
            "speaker": clean_text(self.speaker),
            "talk_title": clean_text(self.talk_title),
            "event_type": clean_text(self.event_type),
            "source": clean_text(self.source),
        }

        summary = template
        for key, value in values.items():
            summary = summary.replace("{" + key + "}", value)

        # Clean punctuation left behind by missing values.
        summary = re.sub(r"\[\s*\]", "", summary)
        summary = re.sub(r"\s+[—–-]\s*$", "", summary)
        summary = re.sub(r"^\s*[—–-]\s+", "", summary)
        summary = re.sub(r"\s+[—–-]\s+(?=[—–-])", " — ", summary)
        summary = re.sub(r"\s{2,}", " ", summary).strip(" —-")

        if summary:
            return summary

        return (
            clean_text(self.raw_title)
            or clean_text(self.talk_title)
            or clean_text(self.program)
            or "Seminar"
        )

    def description_text(self) -> str:
        parts: list[str] = []

        if self.program:
            parts.append(f"Program: {self.program}")
        if self.speaker:
            parts.append(f"Speaker: {self.speaker}")
        if self.affiliation:
            parts.append(f"Affiliation: {self.affiliation}")
        if self.host:
            parts.append(f"Host: {self.host}")
        if self.event_type:
            parts.append(f"Event type: {self.event_type}")
        if self.talk_title:
            parts.append(f"Talk title: {self.talk_title}")
        if self.description:
            parts.append(self.description)

        if self.zoom_urls:
            parts.append("Join via Zoom:\n" + "\n".join(self.zoom_urls))

        parts.append(f"Source: {self.source_url}")
        return "\n\n".join(parts)
