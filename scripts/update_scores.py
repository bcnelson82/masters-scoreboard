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

ESPN_API_URL = "https://site.web.api.espn.com/apis/v2/sports/golf/pga/leaderboard"
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


# ------------------ HELPERS ------------------ #

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


# ------------------ CONFIG ------------------ #

def load_config(path: Path):
    raw = json.loads(path.read_text())
    event = raw["event"]

    teams = []
    for t in raw["teams"]:
        players = []
        for p in t["players"]:
            name = p["name"]
            aliases = p.get("aliases", [name])
            aliases.append(make_short_alias(name))
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


# ------------------ API ------------------ #

def fetch_api(event_id: str):
    r = requests.get(
        ESPN_API_URL,
        params={"event": event_id},
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json() 


def build_api_lookup(payload):
    lookup = {}

    try:
        competitors = payload["events"][0]["competitions"][0]["competitors"]
    except Exception:
        return lookup

    for c in competitors:
        name = c["athlete"]["displayName"]
        score = c.get("score") or "E"

        try:
            score_int = parse_score_to_int(score)
        except:
            score_int = 0

        entry = {
            "name": name,
            "scoreToPar": score_int,
            "scoreDisplay": score_display(score_int),
            "status": "Live",
            "detail": "Live",
            "teeTime": None,
            "found": True,
        }

        keys = [
            normalize_text(name),
            normalize_text(make_short_alias(name)),
        ]

        for k in keys:
            lookup[k] = entry

    return lookup


# ------------------ PAGE PARSER (FIXED) ------------------ #

def build_page_lookup():
    r = requests.get(ESPN_PAGE_URL, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")

    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]

    lookup = {}
    debug_rows = []

    i = 0
    while i < len(lines):

        line = lines[i]

        # MATCH: "Scottie Scheffler E"
        match = re.match(r"^(.*\S)\s+(E|[+-]\d+)$", line)

        if match:
            name = match.group(1).strip()
            score_token = match.group(2)

            score_int = 0 if score_token == "E" else int(score_token)

            detail = "Live"

            # CHECK NEXT LINE FOR HOLE
            if i + 1 < len(lines):
                next_line = lines[i + 1]

                # MATCH: "T21 12"
                thru_match = re.search(r"(F|\d{1,2})$", next_line)

                if thru_match:
                    token = thru_match.group(1)
                    if token == "F":
                        detail = "R1 • Complete"
                    else:
                        detail = f"R1 • Thru {token}"

            entry = {
                "name": name,
                "scoreToPar": score_int,
                "scoreDisplay": score_display(score_int),
                "status": detail,
                "detail": detail,
                "teeTime": None,
                "found": True,
                "sourceLine": line,
            }

            keys = [
                normalize_text(name),
                normalize_text(make_short_alias(name)),
            ]

            for k in keys:
                lookup[k] = entry

            debug_rows.append(f"{line} | {lines[i+1] if i+1 < len(lines) else ''}")

        i += 1

    Path("site/data/rows_debug.txt").write_text("\n".join(debug_rows[:200]))

    return lookup


# ------------------ MERGE ------------------ #

def merge(api_lookup, page_lookup):
    merged = dict(api_lookup)

    for key, page_entry in page_lookup.items():
        if key in merged:
            if "Thru" in page_entry["detail"] or "Complete" in page_entry["detail"]:
                merged[key]["status"] = page_entry["status"]
                merged[key]["detail"] = page_entry["detail"]

    return merged


# ------------------ OUTPUT ------------------ #

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

    return {
        "event": event,
        "meta": {
            "updated": datetime.now(timezone.utc).isoformat()
        },
        "teams": result
    }


# ------------------ MAIN ------------------ #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="teams.json")
    parser.add_argument("--out", default="site/data/latest.json")
    parser.add_argument("--event-id", default="401811941")
    args = parser.parse_args()

    event, teams = load_config(Path(args.config))

    api_lookup = {}
    api_error = None

    try:
        api = fetch_api(args.event_id)
        api_lookup = build_api_lookup(api)
    except Exception as exc:
        api_error = str(exc)

    page_lookup = build_page_lookup()

    merged = merge(api_lookup, page_lookup) if api_lookup else page_lookup

    output = build_output(merged, event, teams)

    if api_error:
        output.setdefault("meta", {})
        output["meta"]["apiFallbackReason"] = api_error

    Path(args.out).write_text(json.dumps(output, indent=2))
    print("Updated scores.")
