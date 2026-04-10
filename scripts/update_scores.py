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
USER_AGENT = "Mozilla/5.0"
ESPN_PAGE_URL = "https://www.espn.com/golf/leaderboard/_/tournamentId/401811941"


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
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
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


def parse_score_to_int(score: str) -> int:
    if score == "E":
        return 0
    return int(score)


def load_config(path: Path) -> tuple[dict, list[TeamConfig]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    event = raw["event"]

    teams: list[TeamConfig] = []
    for t in raw["teams"]:
        players: list[PlayerConfig] = []
        for p in t["players"]:
            name = p["name"]
            aliases = list(p.get("aliases", [name]))
            short_alias = make_short_alias(name)
            if short_alias not in aliases:
                aliases.append(short_alias)
            players.append(PlayerConfig(name=name, aliases=aliases))

        teams.append(
            TeamConfig(
                slug=t["slug"],
                display_name=t["displayName"],
                subtitle=t.get("subtitle", ""),
                players=players,
            )
        )

    return event, teams


def fetch_page_text() -> list[str]:
    response = requests.get(
        ESPN_PAGE_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text("\n")
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def build_page_lookup(lines: list[str]) -> tuple[dict[str, dict], list[str]]:
    lookup: dict[str, dict] = {}
    debug_rows: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip obvious non-table lines
        if line.upper() in {
            "LEADERBOARD",
            "PLAYER STATS",
            "COURSE STATS",
            "POS",
            "PLAYER",
            "SCORE",
            "TODAY",
            "THRU",
            "R1",
            "R2",
            "R3",
            "R4",
            "TOT",
            "AUTO UPDATE:",
            "ON",
        }:
            i += 1
            continue

        # New ESPN structure:
        # 1
        # -
        # Rory McIlroy
        # -10
        # -5
        # 16
        # 66
        # 20
        # -- ...
        if re.fullmatch(r"T?\d+|-", line):
            if i + 5 < len(lines):
                maybe_dash = lines[i + 1].strip()
                name = lines[i + 2].strip()
                score_token = lines[i + 3].strip().upper()
                today_token = lines[i + 4].strip().upper()
                thru_token = lines[i + 5].strip().upper()

                # Validate this looks like a player block
                if (
                    re.search(r"[A-Za-z]", name)
                    and (
                        score_token == "E"
                        or score_token == "-"
                        or re.fullmatch(r"[+-]\d+", score_token)
                    )
                ):
                    score_int = 0 if score_token in {"E", "-"} else parse_score_to_int(score_token)
                    tee_time = None
                    detail = "Live"

                    if re.fullmatch(r"\d{1,2}:\d{2}", thru_token) and i + 6 < len(lines):
                        ampm = lines[i + 6].strip().upper()
                        if ampm in {"AM", "PM"}:
                            tee_time = f"{thru_token} {ampm}"
                            detail = f"Tee time {tee_time}"
                            score_int = 0
                    elif thru_token == "F":
                        detail = "R2 • Complete"
                    elif re.fullmatch(r"\d{1,2}", thru_token):
                        detail = f"R2 • Thru {thru_token}"

                    source_line = " | ".join(
                        [
                            line,
                            maybe_dash,
                            name,
                            score_token,
                            today_token,
                            thru_token,
                        ]
                    )

                    entry = {
                        "name": name,
                        "scoreToPar": score_int,
                        "scoreDisplay": score_display(score_int),
                        "status": detail,
                        "detail": detail,
                        "teeTime": tee_time,
                        "found": True,
                        "sourceLine": source_line,
                    }

                    for key in {
                        normalize_text(name),
                        normalize_text(make_short_alias(name)),
                    }:
                        lookup[key] = entry

                    debug_rows.append(source_line)
                    i += 6
                    continue

        i += 1

    return lookup, debug_rows


def build_output(lookup: dict[str, dict], event: dict, teams: list[TeamConfig]) -> dict:
    result = []

    for team in teams:
        players = []
        total = 0

        for player in team.players:
            found = None
            candidates = [player.name, *player.aliases]

            short_alias = make_short_alias(player.name)
            if short_alias not in candidates:
                candidates.append(short_alias)

            for alias in candidates:
                key = normalize_text(alias)
                if key in lookup:
                    found = lookup[key]
                    break

            if not found:
                found = {
                    "name": player.name,
                    "scoreToPar": 0,
                    "scoreDisplay": "E",
                    "status": "Missing",
                    "detail": "Missing",
                    "teeTime": None,
                    "found": False,
                    "sourceLine": None,
                }

            total += int(found["scoreToPar"])
            players.append(found)

        result.append(
            {
                "slug": team.slug,
                "displayName": team.display_name,
                "subtitle": team.subtitle,
                "totalScoreToPar": total,
                "totalScoreDisplay": score_display(total),
                "players": players,
            }
        )

    leader_slug = None
    margin = None
    if len(result) >= 2:
        sorted_totals = sorted(result, key=lambda t: t["totalScoreToPar"])
        leader_slug = sorted_totals[0]["slug"]
        margin = sorted_totals[1]["totalScoreToPar"] - sorted_totals[0]["totalScoreToPar"]

    return {
        "event": {
            "name": event["name"],
            "par": event["par"],
            "scoringRule": event["scoringRule"],
            "refreshMinutes": event["refreshMinutes"],
            "sourceUrl": ESPN_PAGE_URL,
            "mode": "page-only",
        },
        "meta": {
            "fetchedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "leaderSlug": leader_slug,
            "margin": margin,
        },
        "teams": result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="teams.json")
    parser.add_argument("--out", default="site/data/latest.json")
    parser.add_argument("--event-id", default="401811941")  # kept for workflow compatibility
    args = parser.parse_args()

    event, teams = load_config(Path(args.config))
    out_path = Path(args.out)

    Path("site/data").mkdir(parents=True, exist_ok=True)

    lines = fetch_page_text()
    Path("site/data/raw_lines_debug.txt").write_text("\n".join(lines[:800]), encoding="utf-8")

    lookup, debug_rows = build_page_lookup(lines)
    Path("site/data/rows_debug.txt").write_text("\n".join(debug_rows[:200]), encoding="utf-8")

    output = build_output(lookup, event, teams)

    found_count = sum(
        1
        for team in output["teams"]
        for player in team["players"]
        if player.get("found")
    )

    if found_count == 0 and out_path.exists():
        print("No players parsed; keeping previous latest.json")
        return

    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Updated scores. Parsed {found_count} players.")


if __name__ == "__main__":
    main()
