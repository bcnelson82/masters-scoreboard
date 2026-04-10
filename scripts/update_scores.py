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


def build_page_lookup() -> tuple[dict[str, dict], list[str]]:
    lines = fetch_page_text()

    lookup: dict[str, dict] = {}
    debug_rows: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Pattern A:
        # "Scottie Scheffler E"
        # next line: "T21 12"
        # or next line: "5:44 PM"
        match = re.match(r"^(.*\S)\s+(E|[+-]\d+|-)$", line)
        if match:
            name = match.group(1).strip()
            score_token = match.group(2).upper()

            # Skip obvious non-player/header lines
            if name.upper() not in {
                "POS PLAYER SCORE TODAY THRU",
                "LEADERBOARD",
                "PLAYER STATS",
                "COURSE STATS",
            }:
                score_int = 0 if score_token in {"E", "-"} else parse_score_to_int(score_token)
                detail = "Live"
                tee_time = None
                source_line = line

                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()

                    # Tee time line examples:
                    # "5:44 PM"
                    # "- 5:44 PM"
                    tee_match = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", next_line.upper())
                    if tee_match:
                        tee_time = tee_match.group(1).replace("  ", " ")
                        detail = f"Tee time {tee_time}"
                        score_int = 0
                        source_line = f"{line} | {next_line}"
                    else:
                        # Hole / finish line examples:
                        # "T21 12"
                        # "E 17"
                        # "-2 14"
                        # "F"
                        thru_match = re.search(r"(?:T?\d+\s+)?(F|\d{1,2})$", next_line.upper())
                        if thru_match:
                            token = thru_match.group(1)
                            if token == "F":
                                detail = "R1 • Complete"
                            else:
                                detail = f"R1 • Thru {token}"
                            source_line = f"{line} | {next_line}"

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

    lookup, debug_rows = build_page_lookup()
    Path("site/data/rows_debug.txt").write_text("\n".join(debug_rows[:200]), encoding="utf-8")

    output = build_output(lookup, event, teams)

    found_count = sum(
        1
        for team in output["teams"]
        for player in team["players"]
        if player.get("found")
    )

    # Don't overwrite a previously good file with an empty scrape
    if found_count == 0 and out_path.exists():
        print("No players parsed; keeping previous latest.json")
        return

    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Updated scores. Parsed {found_count} players.")


if __name__ == "__main__":
    main()
