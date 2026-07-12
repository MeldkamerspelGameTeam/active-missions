#!/usr/bin/env python3
"""Generate a markdown report grouping missions by requirements keys."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT_DIR / "data" / "inzetten.json"
DEFAULT_SEEN_INPUT = ROOT_DIR / "data" / "inzetten_ids.json"
DEFAULT_OUTPUT = ROOT_DIR / "missions_grouped_by_requirement.md"


def natural_id_key(value: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    token = ""
    for char in value:
        if char.isdigit():
            if token and not token[-1].isdigit():
                parts.append(token.lower())
                token = ""
            token += char
        else:
            if token and token[-1].isdigit():
                parts.append(int(token))
                token = ""
            token += char
    if token:
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token.lower())
    return tuple(parts)


def build_table(missions: list[dict[str, Any]]) -> str:
    headers = ["ID", "Name", "Avg Credits", "Last Seen"]
    rows = [
        [
            str(mission.get("id", "")).replace("|", "\\|"),
            str(mission.get("name", "")).replace("|", "\\|"),
            str(mission.get("average_credits", "")).replace("|", "\\|"),
            format_seen_for_display(mission.get("last_seen", "")).replace("|", "\\|"),
        ]
        for mission in missions
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(values[i].ljust(widths[i]) for i in range(len(values))) + " |"

    align = [
        "-" * widths[0],
        "-" * widths[1],
        "-" * max(3, widths[2] - 1) + ":",
        "-" * widths[3],
    ]

    lines = [render_row(headers), "| " + " | ".join(align) + " |"]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def append_non_empty_section(lines: list[str], title: str, missions: list[dict[str, Any]]) -> None:
    if not missions:
        return

    lines.append(title)
    lines.append("")
    lines.append(build_table(missions))
    lines.append("")


def parse_seen_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "never":
        return None

    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_seen_for_display(value: Any) -> str:
    if not isinstance(value, str):
        return str(value)

    seen_dt = parse_seen_date(value)
    if seen_dt is None:
        return value

    return seen_dt.strftime("%d-%m-%Y")


def classify_seen_bucket(last_seen: Any, now_utc: datetime, days: int) -> str:
    if not isinstance(last_seen, str) or not last_seen.strip() or last_seen == "never":
        return "never_seen"

    seen_dt = parse_seen_date(last_seen)
    if seen_dt is None:
        return "old_seen"

    cutoff = now_utc - timedelta(days=days)
    if cutoff <= seen_dt <= now_utc:
        return "last_seen_30_days"
    return "old_seen"


def load_last_seen_map(ids_payload: Any) -> dict[str, str]:
    if not isinstance(ids_payload, list):
        return {}

    last_seen_map: dict[str, str] = {}
    for item in ids_payload:
        if not isinstance(item, dict):
            continue

        mission_id = item.get("id")
        last_seen = item.get("last_seen")
        if mission_id is None:
            continue

        last_seen_map[str(mission_id)] = str(last_seen) if last_seen is not None else "never"

    return last_seen_map


def split_by_seen_bucket(missions: list[dict[str, Any]], now_utc: datetime, days: int) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "last_seen_30_days": [],
        "old_seen": [],
        "never_seen": [],
    }

    for mission in missions:
        bucket = classify_seen_bucket(mission.get("last_seen"), now_utc, days)
        buckets[bucket].append(mission)

    for bucket_missions in buckets.values():
        bucket_missions.sort(key=lambda mission: natural_id_key(str(mission.get("id", ""))))

    return buckets


def generate_report(payload: Any, last_seen_map: dict[str, str], days: int = 30) -> str:
    if not isinstance(payload, list):
        raise ValueError(f"Expected list payload, got {type(payload).__name__}")

    now_utc = datetime.now(timezone.utc)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    no_requirements: list[dict[str, Any]] = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        mission_id = str(item.get("id", ""))
        enriched_item = dict(item)
        enriched_item["last_seen"] = last_seen_map.get(mission_id, "never")

        requirements = item.get("requirements")
        if isinstance(requirements, dict) and requirements:
            for req_key, req_value in requirements.items():
                if not isinstance(req_key, str) or not req_key.strip():
                    continue
                req_item = dict(enriched_item)
                req_item["required_count"] = req_value
                grouped[req_key.strip().lower()].append(req_item)
        else:
            no_requirements.append(enriched_item)

    lines: list[str] = [
        "# Missions Grouped By Requirement Key",
        "",
        f"Total missions: {len(payload)}",
        f"Requirement groups: {len(grouped)}",
        "",
        "A mission can appear in multiple requirement sections.",
        f"Seen split: last {days} days, old seen, and never seen.",
        "",
    ]

    for req_key in sorted(grouped.keys()):
        missions = grouped[req_key]
        buckets = split_by_seen_bucket(missions, now_utc, days)
        recent = buckets["last_seen_30_days"]
        old = buckets["old_seen"]
        never = buckets["never_seen"]

        lines.append(f"## {req_key} (Count: {len(missions)})")
        lines.append("")
        if recent:
            lines.append(f"- Last seen {days} days: {len(recent)}")
        if old:
            lines.append(f"- Old seen: {len(old)}")
        if never:
            lines.append(f"- Never seen: {len(never)}")
        lines.append("")

        append_non_empty_section(lines, f"### Last Seen {days} Days", recent)
        append_non_empty_section(lines, "### Old Seen", old)
        append_non_empty_section(lines, "### Never Seen", never)
        lines.append("")

    if no_requirements:
        buckets = split_by_seen_bucket(no_requirements, now_utc, days)
        recent = buckets["last_seen_30_days"]
        old = buckets["old_seen"]
        never = buckets["never_seen"]

        lines.append(f"## no_requirements (Count: {len(no_requirements)})")
        lines.append("")
        if recent:
            lines.append(f"- Last seen {days} days: {len(recent)}")
        if old:
            lines.append(f"- Old seen: {len(old)}")
        if never:
            lines.append(f"- Never seen: {len(never)}")
        lines.append("")

        append_non_empty_section(lines, f"### Last Seen {days} Days", recent)
        append_non_empty_section(lines, "### Old Seen", old)
        append_non_empty_section(lines, "### Never Seen", never)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Group missions by requirements keys and save markdown report.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Path to inzetten.json")
    parser.add_argument("--seen-input", type=Path, default=DEFAULT_SEEN_INPUT, help="Path to inzetten_ids.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to output markdown report")
    parser.add_argument("--days", type=int, default=30, help="Recent seen window in days (default: 30)")
    args = parser.parse_args()

    if args.days <= 0:
        raise ValueError("--days must be greater than 0")

    with args.input.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    with args.seen_input.open("r", encoding="utf-8") as file:
        seen_payload = json.load(file)

    last_seen_map = load_last_seen_map(seen_payload)

    report = generate_report(payload, last_seen_map, days=args.days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        file.write(report)

    print(f"Saved requirement report -> {args.output}")


if __name__ == "__main__":
    main()
