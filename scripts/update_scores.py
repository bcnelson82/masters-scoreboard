#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (compatible; MastersTeamScoreboard/1.0; "
    "+https://github.com/)"
)

ESPN_API_URL = "https://site.web.api.espn.com/apis/v2/sports/golf/pga/leaderboard"
ESPN_PAGE_URL = "https://www.espn.com/golf/leaderboard?season=2025&tournamentId=401811941"
STATUS_TOKENS = {"CUT", "WD", "DQ", "MDF", "DNS"}


@dataclass
class PlayerConfig:
    name: str
    aliases: list[str]


@dataclass
class TeamConfig:
    slug: str
    display_name: str
    subtitle: str
    players: list[PlayerConfig]


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.lower().replace("’", "'")
    lowered = re.sub(r"\(a\)", "", lowered)
    lowered = re.sub(r"[^a-z0-9:+\- ]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def score_display(value: int) -> str:
    if value == 0:
        return "E"
    if value > 0:
        return f"+{value}"
    return str(value)


def make_short_alias(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate landing-page JSON from ESPN golf data."
    )
    parser.add_argument("--config", default="teams.json", help="Path to teams.json")
    parser.add_argument("--out", default="site/data/latest.json", help="Output JSON path")
    parser.add_argument("--url", default=None, help="Optional override URL")
    parser.add_argument("--event-id", default="401811941", help="ESPN event id")
    return parser.parse_args()


def load_config(config_path: Path) -> tuple[dict, list[TeamConfig]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    event = raw["event"]
    teams: list[TeamConfig] = []

    for team in raw["teams"]:
        players: list[PlayerConfig] = []
        for player in team["players"]:
            name = player["name"]
            aliases = list(player.get("aliases", [name]))
            short_alias = make_short_alias(name)
            if short_alias not in aliases:
                aliases.append(short_alias)
            players.append(PlayerConfig(name=name, aliases=aliases))

        teams.append(
            TeamConfig(
                slug=team["slug"],
                display_name=team["displayName"],
                subtitle=team.get("subtitle", ""),
                players=players,
            )
        )

    return event, teams


def parse_score_to_int(score_value: str | None) -> int:
    if not score_value:
        return 0

    value = str(score_value).strip().upper()

    if value in {"E", "EVEN"}:
        return 0
    if re.fullmatch(r"[+-]\d+", value):
        return int(value)
    if re.fullmatch(r"\d+", value):
        return int(value)

    return 0


def normalize_status(detail: str | None) -> tuple[str, str | None]:
    if not detail:
        return "Live", None

    text = re.sub(r"\s+", " ", detail).strip()
    upper = text.upper()

    tee_match = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", upper)
    if tee_match and ("TEE" in upper or upper.startswith("-")):
        tee_time = tee_match.group(1).replace("  ", " ")
        return f"Tee time {tee_time}", tee_time

    if upper in {"FINAL", "F"}:
        return "Final", None

    # Handles strings like "Round 1 - In Progress, Thru 12"
    round_thru = re.search(r"ROUND\s+(\d).*?THRU\s+(\d{1,2})", upper)
    if round_thru:
        return f"R{round_thru.group(1)} • Thru {round_thru.group(2)}", None

    # Handles strings like "Thru 12"
    thru_match = re.search(r"\bTHRU\s+(\d{1,2})\b", upper)
    if thru_match:
        return f"Thru {thru_match.group(1)}", None

    # Handles compact ESPN forms like "-2(11)" or "E(F)"
    compact_match = re.search(r"\(([0-9]{1,2}|F)\)", upper)
    if compact_match:
        token = compact_match.group(1)
        if token == "F":
            return "Round complete", None
        return f"Thru {token}", None

    if upper in {"CUT", "WD", "DQ", "MDF", "DNS"}:
        return upper, None

    return text.title(), None


def fetch_api_payload(api_url: str, event_id: str) -> dict:
    response = requests.get(
        api_url,
        params={"event": event_id},
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def get_competitors(payload: dict) -> list[dict]:
    events = payload.get("events") or []
    if not events:
        return []

    event = events[0]
    competitions = event.get("competitions") or []
    if not competitions:
        return []

    competition = competitions[0]
    return competition.get("competitors") or []


def extract_player_entry(competitor: dict) -> dict:
    athlete = competitor.get("athlete") or {}
    status = competitor.get("status") or {}
    status_type = status.get("type") or {}

    name = athlete.get("displayName") or athlete.get("shortName") or "Unknown"

    raw_score = competitor.get("score")
    if raw_score in (None, "", "-"):
        raw_score = (
            competitor.get("toPar")
            or competitor.get("statistics", [{}])[0].get("displayValue")
            if competitor.get("statistics")
            else None
        )

    raw_score_str = str(raw_score).strip() if raw_score not in (None, "") else "E"
    if raw_score_str == "-":
        raw_score_str = "E"

    score_int = parse_score_to_int(raw_score_str)

    detail = (
        status_type.get("detail")
        or status.get("displayClock")
        or competitor.get("displayStatus")
        or ""
    )

    normalized_detail, tee_time = normalize_status(detail)

    return {
        "name": name,
        "normalizedName": normalize_text(name),
        "scoreToPar": score_int,
        "scoreDisplay": score_display(score_int),
        "status": normalized_detail,
        "detail": normalized_detail,
        "teeTime": tee_time,
        "found": True,
        "sourceLine": json.dumps(
            {"name": name, "score": raw_score_str, "detail": detail},
            ensure_ascii=False,
        ),
    }


def build_player_lookup_from_api(competitors: list[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}

    for competitor in competitors:
        entry = extract_player_entry(competitor)
        keys = {
            entry["normalizedName"],
            normalize_text(entry["name"]),
            normalize_text(make_short_alias(entry["name"])),
        }
        for key in keys:
            if key and key not in lookup:
                lookup[key] = entry

    return lookup


def html_to_lines(raw_text: str) -> list[str]:
    soup = BeautifulSoup(raw_text, "html.parser")
    text = soup.get_text("\n") if "<" in raw_text else raw_text
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def choose_score_section(lines: list[str]) -> tuple[list[str], str]:
    for i, line in enumerate(lines):
        if "POS PLAYER SCORE TODAY THRU" in line.upper():
            section = []
            for row in lines[i + 1:]:
                upper = row.upper()
                if upper.startswith("GLOSSARY") or upper.startswith("LATEST GOLF VIDEOS"):
                    break
                section.append(row)
            return section, "leaderboard"

    joined = "\n".join(lines).lower()
    if "tee time" in joined:
        return lines, "tee-times"

    return lines, "unknown"


def is_position_line(line: str) -> bool:
    return bool(re.fullmatch(r"(T?\d+|[-])\.?", line.strip()))


def is_score_block_line(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if re.match(r"^(?:E|[+-]\d+|-)\b", line):
        return True
    if re.search(r"\b(?:F|\d{1,2}:\d{2}|\d{1,2})\b", line):
        return True
    if re.search(r"--\s+--\s+--", line):
        return True
    return False


def build_leaderboard_rows(lines: list[str]) -> list[str]:
    rows: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.upper().startswith("GLOSSARY") or line.upper().startswith("LATEST GOLF VIDEOS"):
            break

        if is_position_line(line):
            if i + 2 < len(lines):
                name_line = lines[i + 1].strip()
                stats_line = lines[i + 2].strip()

                if name_line and stats_line and is_score_block_line(stats_line):
                    rows.append(f"{name_line} {stats_line}")
                    i += 3
                    continue

        if i + 1 < len(lines):
            name_line = lines[i].strip()
            stats_line = lines[i + 1].strip()

            if (
                name_line
                and stats_line
                and not is_position_line(name_line)
                and is_score_block_line(stats_line)
                and re.search(r"[A-Za-z]", name_line)
            ):
                rows.append(f"{name_line} {stats_line}")
                i += 2
                continue

        i += 1

    return rows


def build_player_lookup_from_page(page_url: str) -> tuple[dict[str, dict], dict]:
    response = requests.get(
        page_url,
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    lines = html_to_lines(response.text)
    section, mode = choose_score_section(lines)
    rows = build_leaderboard_rows(section)

    lookup: dict[str, dict] = {}

    for row in rows:
        parts = row.split()
        # Split name from score block by locating first score token
        score_start = None
        for idx, token in enumerate(parts):
            upper = token.upper()
            if upper == "E" or re.fullmatch(r"[+-]\d+", upper) or token == "-":
                score_start = idx
                break

        if score_start is None:
            continue

        name = " ".join(parts[:score_start]).strip()
        if not name:
            continue

        score_token = parts[score_start].upper()
        score_int = 0 if score_token in {"E", "-"} else parse_score_to_int(score_token)

        trailing = parts[score_start + 1:]
        detail = "Live"
        tee_time = None

        if len(parts) >= score_start + 4 and parts[score_start] == "-" and parts[score_start + 1] == "-":
            tee_time = f"{parts[score_start + 2]} {parts[score_start + 3].upper()}"
            detail = f"Tee time {tee_time}"
            score_int = 0
        else:
            if trailing:
                if trailing[0].upper() == "F":
                    detail = "R1 • Complete"
                elif re.fullmatch(r"\d{1,2}", trailing[0]):
                    detail = f"R1 • Thru {trailing[0]}"
                    
        entry = {
            "name": name,
            "normalizedName": normalize_text(name),
            "scoreToPar": score_int,
            "scoreDisplay": score_display(score_int),
            "status": detail,
            "detail": detail,
            "teeTime": tee_time,
            "found": True,
            "sourceLine": row,
        }

        keys = {
            entry["normalizedName"],
            normalize_text(name),
            normalize_text(make_short_alias(name)),
        }
        for key in keys:
            if key and key not in lookup:
                lookup[key] = entry

    debug = {
        "mode": mode,
        "rowsBuilt": len(rows),
        "sampleRows": rows[:25],
    }
    return lookup, debug


def find_player(player: PlayerConfig, lookup: dict[str, dict]) -> dict | None:
    candidates = [player.name, *player.aliases]

    short_alias = make_short_alias(player.name)
    if short_alias not in candidates:
        candidates.append(short_alias)

    for alias in candidates:
        normalized = normalize_text(alias)
        if normalized in lookup:
            return lookup[normalized]

    return None

def merge_lookups(api_lookup: dict[str, dict], page_lookup: dict[str, dict], teams: list[TeamConfig]) -> dict[str, dict]:
    merged = dict(api_lookup)

    for team in teams:
        for player in team.players:
            candidates = [player.name, *player.aliases]
            short_alias = make_short_alias(player.name)
            if short_alias not in candidates:
                candidates.append(short_alias)

            best_api = None
            best_page = None

            for alias in candidates:
                key = normalize_text(alias)
                if best_api is None and key in api_lookup:
                    best_api = api_lookup[key]
                if best_page is None and key in page_lookup:
                    best_page = page_lookup[key]

            if best_api and best_page:
                # Keep API score, but use page detail if page has something more useful than generic "Live"
                if best_page.get("detail") and best_page.get("detail") not in {"Live", "Missing"}:
                    merged_entry = dict(best_api)
                    merged_entry["status"] = best_page["status"]
                    merged_entry["detail"] = best_page["detail"]
                    merged_entry["teeTime"] = best_page.get("teeTime")
                    merged_entry["sourceLine"] = best_page.get("sourceLine")
                    for alias in candidates:
                        merged[normalize_text(alias)] = merged_entry

    return merged

def build_output_from_lookup(lookup: dict[str, dict], event: dict, teams: list[TeamConfig], source_url: str, mode: str) -> dict:
    rendered_teams: list[dict] = []

    for team in teams:
        rendered_players: list[dict] = []
        total = 0

        for player in team.players:
            matched = find_player(player, lookup)

            if matched is None:
                state = {
                    "name": player.name,
                    "scoreToPar": 0,
                    "scoreDisplay": "E",
                    "status": "Missing",
                    "detail": "Player was not found in the source response.",
                    "teeTime": None,
                    "found": False,
                    "sourceLine": None,
                }
            else:
                state = {
                    "name": player.name,
                    "scoreToPar": matched["scoreToPar"],
                    "scoreDisplay": matched["scoreDisplay"],
                    "status": matched["status"],
                    "detail": matched["detail"],
                    "teeTime": matched["teeTime"],
                    "found": True,
                    "sourceLine": matched["sourceLine"],
                }

            total += int(state["scoreToPar"])
            rendered_players.append(state)

        rendered_teams.append(
            {
                "slug": team.slug,
                "displayName": team.display_name,
                "subtitle": team.subtitle,
                "totalScoreToPar": total,
                "totalScoreDisplay": score_display(total),
                "players": rendered_players,
            }
        )

    sorted_totals = sorted(rendered_teams, key=lambda team: team["totalScoreToPar"])
    leader_slug = sorted_totals[0]["slug"] if sorted_totals else None
    margin = None
    if len(sorted_totals) >= 2:
        margin = sorted_totals[1]["totalScoreToPar"] - sorted_totals[0]["totalScoreToPar"]

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return {
        "event": {
            "name": event["name"],
            "par": event["par"],
            "scoringRule": event["scoringRule"],
            "refreshMinutes": event["refreshMinutes"],
            "sourceUrl": source_url,
            "mode": mode,
        },
        "meta": {
            "fetchedAtUtc": fetched_at,
            "leaderSlug": leader_slug,
            "margin": margin,
        },
        "teams": rendered_teams,
    }


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    out_path = Path(args.out)

    event, teams = load_config(config_path)

    debug_dir = Path("site/data")
    debug_dir.mkdir(parents=True, exist_ok=True)

    api_url = ESPN_API_URL
    event_id = args.event_id

    try:
        payload = fetch_api_payload(api_url, event_id)
        (debug_dir / "api_payload_debug.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        competitors = get_competitors(payload)
        api_lookup = build_player_lookup_from_api(competitors)

        # Build page lookup too, to improve hole / round / tee-time detail
        page_lookup, debug = build_player_lookup_from_page(ESPN_PAGE_URL)
        (debug_dir / "page_fallback_debug.json").write_text(
            json.dumps(debug, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        merged_lookup = merge_lookups(api_lookup, page_lookup, teams)
        output = build_output_from_lookup(merged_lookup, event, teams, ESPN_PAGE_URL, "api+page-status")

    except Exception as exc:
        page_lookup, debug = build_player_lookup_from_page(ESPN_PAGE_URL)
        (debug_dir / "page_fallback_debug.json").write_text(
            json.dumps(debug, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        output = build_output_from_lookup(page_lookup, event, teams, ESPN_PAGE_URL, "page-fallback")
        output["meta"]["apiFallbackReason"] = str(exc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
