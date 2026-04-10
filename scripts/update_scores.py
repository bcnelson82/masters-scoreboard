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
    lowered = without_marks.lower()
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


def load_config(path: Path):
    raw = json.loads(path.read_text())
    event = raw["event"]

    teams = []
    for t in raw["teams"]:
        players = []
        for p in t["players"]:
            name = p["name"]
            aliases = p.get("aliases", [name])
            short = make_short_alias(name)
            if short not in aliases:
                aliases.append(short)
            players.append(PlayerConfig(name, aliases))

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
    r = requests.get(ESPN_PAGE_URL, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]
    return lines


def choose_score_section(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if "POS PLAYER SCORE TODAY THRU" in line.upper():
            section = []
            for row in lines[i + 1:]:
                upper = row.upper()
                if upper.startswith("GLOSSARY") or upper.startswith("LATEST GOLF VIDEOS"):
                    break
                section.append(row)
            return section
    return lines


def build_page_lookup() -> tuple[dict[str, dict], list[str]]:
    lines = fetch_page_text()
    section = choose_score_section(lines)

    lookup = {}
    debug_rows = []

    i = 0
    while i < len(section):
        line = section[i]

        # Case 1:
        # Scottie Scheffler E
        # T21 12
        # or Justin Thomas -
        # - 1:32 PM
        m = re.match(r"^(.*\S)\s+(E|[+-]\d+|-)$", line)
        if m:
            name = m.group(1).strip()
            score_token = m.group(2).upper()
            score_int = 0 if score_token in {"E", "-"} else parse_score_to_int(score_token)

            detail = "Live"
            tee_time = None
            source_line = line

            if i + 1 < len(section):
                next_line = section[i + 1].strip()

                tee_match = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", next_line.upper())
                if tee_match:
                    tee_time = tee_match.group(1).replace("  ", " ")
                    detail = f"Tee time {tee_time}"
                    score_int = 0
                    source_line = f"{line} | {next_line}"
                else:
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
            continue

        # Case 2:
        # Rory McIlroy -5 -5 10 -- -- -- -- 31
        parts = line.split()
        score_start = None
        for idx, token in enumerate(parts):
            upper = token.upper()
            if upper == "E" or re.fullmatch(r"[+-]\d+", upper) or token == "-":
                score_start = idx
                break

        if score_start is not None:
            name = " ".join(parts[:score_start]).strip()
            if name:
                score_token = parts[score_start].upper()
                score_int = 0 if score_token in {"E", "-"} else parse_score_to_int(score_token)
                trailing = parts[score_start + 1:]

                detail = "Live"
                tee_time = None

                if len(parts) >= score_start + 4 and parts[score_start] == "-" and parts[score_start + 1] == "-":
                    tee_time = f"{parts[score_start + 2]} {parts[score_start + 3].upper()}"
                    detail = f"Tee time {tee_time}"
                    score_int = 0
                elif len(trailing) >= 2:
                    thru_token = trailing[1].upper()
                    if thru_token == "F":
                        detail = "R1 • Complete"
                    elif re.fullmatch(r"\d{1,2}", thru_token):
                        detail = f"R1 • Thru {thru_token}"

                entry = {
                    "name": name,
                    "scoreToPar": score_int,
                    "scoreDisplay": score_display(score_int),
                    "status": detail,
                    "detail": detail,
                    "teeTime": tee_time,
                    "found": True,
                    "sourceLine": line,
                }

                for key in {
                    normalize_text(name),
                    normalize_text(make_short_alias(name)),
                }:
                    lookup[key] = entry

                debug_rows.append(line)

        i += 1

    return lookup, debug_rows


def build_output(lookup, event, teams):
    result = []

    for team in teams:
        players = []
        total = 0

        for p in team.players:
            found = None
            for alias in [p.name] + p.aliases:
                key = normalize_text(alias)
                if key in lookup:
                    found = lookup[key]
                    break

            if not found:
                found = {
                    "name": p.name,
                    "scoreToPar": 0,
                    "scoreDisplay": "E",
                    "status": "Missing",
                    "detail": "Missing",
                    "teeTime": None,
                    "found": False,
                    "sourceLine": None,
                }

            total += found["scoreToPar"]
            players.append(found)

        result.append({
            "slug": team.slug,
            "displayName": team.display_name,
            "totalScoreToPar": total,
            "totalScoreDisplay": score_display(total),
            "players": players
        })

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="teams.json")
    parser.add_argument("--out", default="site/data/latest.json")
    parser.add_argument("--event-id", default="401811941")  # ignored, kept for workflow compatibility
    args = parser.parse_args()

    event, teams = load_config(Path(args.config))
    lookup, debug_rows = build_page_lookup()

    Path("site/data").mkdir(parents=True, exist_ok=True)
    Path("site/data/rows_debug.txt").write_text("\n".join(debug_rows[:200]))

    output = build_output(lookup, event, teams)

    Path(args.out).write_text(json.dumps(output, indent=2))
    print("Updated scores.")


if __name__ == "__main__":
    main()
