#!/usr/bin/env python3
"""Download mission sources and store each result as a JSON file."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCES = {
    "inzetten": "https://github.com/Piet2001/Inzetten/raw/refs/heads/main/inzetten.json",
    "missions_log": "https://piet2001-mks.hf.space/missions/log",
}

OUTPUT_DIR = Path(__file__).resolve().parent / "data"


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; data-fetcher/1.0)",
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_to_json(text: str) -> Any:
    # Prefer regular JSON payloads.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to JSON Lines if the endpoint returns line-based logs.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        parsed_lines = []
        for line in lines:
            try:
                parsed_lines.append(json.loads(line))
            except json.JSONDecodeError:
                break
        else:
            return parsed_lines

    # Last resort: keep the raw payload inside a JSON object.
    return {"raw": text}


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(content)


def sort_missions_log(payload: Any) -> Any:
    if not isinstance(payload, list):
        return payload

    def natural_parts(value: str) -> tuple[Any, ...]:
        parts = re.split(r"(\d+)", value)
        key_parts: list[Any] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key_parts.append((0, int(part)))
            else:
                key_parts.append((1, part.lower()))
        return tuple(key_parts)

    def mission_key(item: Any) -> tuple[int, str]:
        if isinstance(item, dict) and "mission" in item:
            return (0, natural_parts(str(item["mission"])))
        return (1, tuple())

    return sorted(payload, key=mission_key)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_inzet_active_now(item: Any, now_utc: datetime) -> bool:
    if not isinstance(item, dict):
        return False

    additional = item.get("additional")
    if not isinstance(additional, dict):
        return True

    start = parse_iso_datetime(additional.get("date_start"))
    end = parse_iso_datetime(additional.get("date_end"))

    # Include always-on missions that do not define an active date window.
    if start is None and end is None:
        return True

    if start is not None and end is not None:
        return start <= now_utc <= end

    return False


def build_last_seen_map(missions_log_payload: Any) -> dict[str, str]:
    if not isinstance(missions_log_payload, list):
        return {}

    last_seen: dict[str, str] = {}
    for item in missions_log_payload:
        if not isinstance(item, dict):
            continue

        mission = item.get("mission")
        seen_date = item.get("date")
        if mission is None or not isinstance(seen_date, str) or not seen_date:
            continue

        mission_key = str(mission)
        current = last_seen.get(mission_key)
        if current is None or seen_date > current:
            last_seen[mission_key] = seen_date

    return last_seen


def extract_inzetten_ids(payload: Any, missions_log_payload: Any) -> Any:
    if not isinstance(payload, list):
        return payload

    now_utc = datetime.now(timezone.utc)
    last_seen_map = build_last_seen_map(missions_log_payload)
    ids = []
    for item in payload:
        if isinstance(item, dict) and "id" in item:
            mission_id = str(item["id"])
            active_now = is_inzet_active_now(item, now_utc)
            additional = item.get("additional") if isinstance(item.get("additional"), dict) else {}
            ids.append(
                {
                    "id": mission_id,
                    "name": item.get("name"),
                    "average_credits": item.get("average_credits"),
                    "last_seen": last_seen_map.get(mission_id, "never"),
                    "inactive": not active_now,
                    "date_start": additional.get("date_start"),
                    "date_end": additional.get("date_end"),
                }
            )
    return ids


def parse_seen_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "never":
        return None

    text = value.strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def filter_never_or_stale_missions(payload: Any, now_utc: datetime, days: int = 30) -> Any:
    if not isinstance(payload, list):
        return payload

    cutoff = now_utc - timedelta(days=days)
    filtered = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        last_seen = item.get("last_seen")
        if last_seen == "never":
            filtered.append(item)
            continue

        seen_dt = parse_seen_date(last_seen)
        if seen_dt is not None and seen_dt < cutoff:
            filtered.append(item)

    return filtered


def summarize_missions_markdown(title: str, missions: list[dict[str, Any]]) -> str:
    headers = ["ID", "Name", "Avg Credits", "Last Seen", "Inactive"]

    rows: list[list[str]] = []
    for item in missions:
        rows.append(
            [
                str(item.get("id", "")).replace("|", "\\|"),
                str(item.get("name", "")).replace("|", "\\|"),
                str(item.get("average_credits", "")).replace("|", "\\|"),
                str(item.get("last_seen", "")).replace("|", "\\|"),
                str(item.get("inactive", "")).replace("|", "\\|"),
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(values[i].ljust(widths[i]) for i in range(len(values))) + " |"

    align_parts = [
        "-" * widths[0],
        "-" * widths[1],
        "-" * max(3, widths[2] - 1) + ":",
        "-" * widths[3],
        "-" * widths[4],
    ]
    align_row = "| " + " | ".join(align_parts) + " |"

    lines = [f"# {title}", "", f"Count: {len(missions)}", "", render_row(headers), align_row]
    for row in rows:
        lines.append(render_row(row))

    return "\n".join(lines) + "\n"


def summarize_old_seen_markdown(title: str, missions: list[dict[str, Any]]) -> str:
    active_old_seen = [item for item in missions if not bool(item.get("inactive", False))]
    inactive_old_seen = [item for item in missions if bool(item.get("inactive", False))]

    lines = [f"# {title}", "", f"Count: {len(missions)}", ""]
    lines.append(summarize_missions_markdown("Active old-seen missions", active_old_seen).rstrip())
    lines.append("")
    lines.append(summarize_missions_markdown("Inactive old-seen missions", inactive_old_seen).rstrip())
    lines.append("")

    return "\n".join(lines)


def summarize_inactive_grouped_by_date_markdown(missions: list[dict[str, Any]]) -> str:
    inactive = [item for item in missions if bool(item.get("inactive", False))]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in inactive:
        start = str(item.get("date_start") or "none")
        end = str(item.get("date_end") or "none")
        key = (start, end)
        groups.setdefault(key, []).append(item)

    sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1]))
    lines = [
        "# Inactive missions grouped by date window",
        "",
        f"Inactive missions: {len(inactive)}",
        f"Date window groups: {len(sorted_keys)}",
        "",
    ]

    for start, end in sorted_keys:
        group_items = groups[(start, end)]
        lines.append(f"## Start: {start} | End: {end} | Count: {len(group_items)}")
        lines.append("")
        lines.append(summarize_missions_markdown("Missions", group_items).rstrip())
        lines.append("")

    return "\n".join(lines)


def summarize_readme_markdown(missions: list[dict[str, Any]], never_seen: list[dict[str, Any]], old_seen: list[dict[str, Any]]) -> str:
    total = len(missions)
    inactive_count = sum(1 for item in missions if bool(item.get("inactive", False)))
    active_count = total - inactive_count

    old_seen_inactive = sum(1 for item in old_seen if bool(item.get("inactive", False)))
    old_seen_active = len(old_seen) - old_seen_inactive

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "# Active Missions Report",
        "",
        "This README is auto-updated by main.py and the GitHub workflow.",
        "",
        f"Last updated: {generated_at}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total missions | {total} |",
        f"| Active missions | {active_count} |",
        f"| Inactive missions | {inactive_count} |",
        f"| Never seen missions | {len(never_seen)} |",
        f"| Old seen missions (older than 30 days) | {len(old_seen)} |",
        f"| Old seen active missions | {old_seen_active} |",
        f"| Old seen inactive missions | {old_seen_inactive} |",
        "",
        "## Generated Files",
        "",
        "- data/inzetten.json",
        "- data/missions_log.json",
        "- data/inzetten_ids.json",
        "- never_seen_missions_summary.md",
        "- old_seen_missions_summary.md",
        "- inactive_missions_grouped_by_date.md",
        "",
    ]
    return "\n".join(lines)


def split_never_and_old_missions(payload: Any, now_utc: datetime, days: int = 30) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, list):
        return [], []

    cutoff = now_utc - timedelta(days=days)
    never_seen: list[dict[str, Any]] = []
    old_seen: list[dict[str, Any]] = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        last_seen = item.get("last_seen")
        if last_seen == "never":
            never_seen.append(item)
            continue

        seen_dt = parse_seen_date(last_seen)
        if seen_dt is not None and seen_dt < cutoff:
            old_seen.append(item)

    return never_seen, old_seen


def main() -> None:
    payloads: dict[str, Any] = {}

    for name, url in SOURCES.items():
        try:
            text = fetch_text(url)
            payload = parse_to_json(text)
            if name == "missions_log":
                payload = sort_missions_log(payload)

            payloads[name] = payload
            output_path = OUTPUT_DIR / f"{name}.json"
            save_json(output_path, payload)
            print(f"Saved {url} -> {output_path}")
        except urllib.error.URLError as err:
            print(f"Failed to fetch {url}: {err}")
        except OSError as err:
            print(f"Failed to write {name}.json: {err}")

    if "inzetten" in payloads:
        now_utc = datetime.now(timezone.utc)
        ids_output_path = OUTPUT_DIR / "inzetten_ids.json"
        ids_payload = extract_inzetten_ids(payloads["inzetten"], payloads.get("missions_log"))
        save_json(ids_output_path, ids_payload)
        print(f"Saved derived IDs -> {ids_output_path}")

        never_seen, old_seen = split_never_and_old_missions(ids_payload, now_utc, days=30)

        root_dir = Path(__file__).resolve().parent
        never_seen_output = root_dir / "never_seen_missions_summary.md"
        never_seen_md = summarize_missions_markdown("Never seen missions", never_seen)
        save_text(never_seen_output, never_seen_md)
        print(f"Saved never-seen summary -> {never_seen_output}")

        old_seen_output = root_dir / "old_seen_missions_summary.md"
        old_seen_md = summarize_old_seen_markdown("Old seen missions (older than 30 days)", old_seen)
        save_text(old_seen_output, old_seen_md)
        print(f"Saved old-seen summary -> {old_seen_output}")

        inactive_grouped_output = root_dir / "inactive_missions_grouped_by_date.md"
        inactive_grouped_md = summarize_inactive_grouped_by_date_markdown(ids_payload)
        save_text(inactive_grouped_output, inactive_grouped_md)
        print(f"Saved inactive grouped report -> {inactive_grouped_output}")

        readme_output = root_dir / "README.md"
        readme_md = summarize_readme_markdown(ids_payload, never_seen, old_seen)
        save_text(readme_output, readme_md)
        print(f"Saved README summary -> {readme_output}")


if __name__ == "__main__":
    main()
