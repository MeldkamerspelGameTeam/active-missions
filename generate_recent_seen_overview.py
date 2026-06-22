#!/usr/bin/env python3
"""Generate a grouped overview of missions seen in the last N days."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT_DIR / "data" / "inzetten_ids.json"
DEFAULT_OUTPUT = ROOT_DIR / "missions_seen_last_30_days_grouped.md"


def parse_seen_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "never":
        return None

    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def natural_mission_id_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts = re.split(r"(\d+)", value)
    key_parts: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key_parts.append((0, int(part)))
        else:
            key_parts.append((1, part.lower()))
    return tuple(key_parts)


def compute_table_widths(missions: list[dict[str, Any]]) -> list[int]:
    headers = ["ID", "Name", "Avg Credits", "Inactive"]
    widths = [len(h) for h in headers]
    for item in missions:
        row = [
            str(item.get("id", "")).replace("|", "\\|"),
            str(item.get("name", "")).replace("|", "\\|"),
            str(item.get("average_credits", "")).replace("|", "\\|"),
            str(item.get("inactive", "")).replace("|", "\\|"),
        ]
        for idx, value in enumerate(row):
            widths[idx] = max(widths[idx], len(value))
    return widths


def render_group_table(missions: list[dict[str, Any]], widths: list[int] | None = None) -> str:
    headers = ["ID", "Name", "Avg Credits", "Inactive"]
    rows = [
        [
            str(item.get("id", "")).replace("|", "\\|"),
            str(item.get("name", "")).replace("|", "\\|"),
            str(item.get("average_credits", "")).replace("|", "\\|"),
            str(item.get("inactive", "")).replace("|", "\\|"),
        ]
        for item in missions
    ]

    if widths is None:
        widths = [len(h) for h in headers]
        for row in rows:
            for idx, value in enumerate(row):
                widths[idx] = max(widths[idx], len(value))

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(values[i].ljust(widths[i]) for i in range(len(values))) + " |"

    align_parts = [
        "-" * widths[0],
        "-" * widths[1],
        "-" * max(3, widths[2] - 1) + ":",
        "-" * widths[3],
    ]

    lines = [render_row(headers), "| " + " | ".join(align_parts) + " |"]
    for row in rows:
        lines.append(render_row(row))

    return "\n".join(lines)


def generate_overview(ids_payload: list[dict[str, Any]], days: int) -> str:
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for mission in ids_payload:
        if not isinstance(mission, dict):
            continue

        last_seen = mission.get("last_seen")
        seen_dt = parse_seen_date(last_seen)
        if seen_dt is None:
            continue

        if cutoff <= seen_dt <= now_utc:
            key = seen_dt.strftime("%Y-%m-%d")
            grouped.setdefault(key, []).append(mission)

    sorted_dates = sorted(grouped.keys(), reverse=True)
    total_missions = sum(len(grouped[d]) for d in sorted_dates)

    all_missions = [m for d in sorted_dates for m in grouped[d]]
    global_widths = compute_table_widths(all_missions)

    summary_rows = [[date_key, str(len(grouped[date_key]))] for date_key in sorted_dates]
    date_col_width = max((len(r[0]) for r in summary_rows), default=4)
    count_col_width = max(max((len(r[1]) for r in summary_rows), default=5), 5)
    summary_header = f"| {'Date'.ljust(date_col_width)} | {'Count'.rjust(count_col_width)} |"
    summary_sep = f"| {'-' * date_col_width} | {'-' * (count_col_width - 1) + ':'} |"
    summary_lines = [summary_header, summary_sep] + [
        f"| {r[0].ljust(date_col_width)} | {r[1].rjust(count_col_width)} |" for r in summary_rows
    ]

    lines = [
        f"# Missions Seen In Last {days} Days (Grouped by Last Seen Date)",
        "",
        f"Total missions: {total_missions}",
        f"Date groups: {len(sorted_dates)}",
        "",
        *summary_lines,
        "",
    ]

    for date_key in sorted_dates:
        missions = grouped[date_key]
        missions.sort(key=lambda item: natural_mission_id_key(str(item.get("id", ""))))

        lines.append(f"## {date_key} (Count: {len(missions)})")
        lines.append("")
        lines.append(render_group_table(missions, global_widths))
        lines.append("")

    if not sorted_dates:
        lines.append("No missions were seen in the configured time window.")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate grouped overview for recently seen missions.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Path to inzetten_ids.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to output markdown file")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    args = parser.parse_args()

    if args.days <= 0:
        raise ValueError("--days must be greater than 0")

    with args.input.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError(f"Expected list payload in {args.input}, got {type(payload).__name__}")

    markdown = generate_overview(payload, args.days)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        file.write(markdown)

    print(f"Saved grouped recent-seen overview -> {args.output}")


if __name__ == "__main__":
    main()
