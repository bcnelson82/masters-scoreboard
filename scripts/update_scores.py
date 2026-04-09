#!/usr/bin/env python3
"""Build the latest team scoreboard JSON for the Masters landing page.

Default source: ESPN Masters leaderboard page.

The parser intentionally works from page text rather than fragile CSS selectors so
it remains usable if ESPN changes its markup. It looks for either a leaderboard
section ("POS PLAYER SCORE") or a tee-time section ("PLAYER TEE TIME").
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup


STATUS_TOKENS = {"CUT", "WD", "DQ", "MDF", "DNS"}
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (compatible; MastersTeamScoreboard/1.0; "
    "+https://github.com/)"
)


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



def read_source(url: str | None, input_file: Path | None) -> str:
    if input_file is not None:
        return input_file.read_text(encoding="utf-8")

    if not url:
        raise ValueError("A URL is required when --input-file is not provided.")

    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.text



def html_to_lines(raw_text: str) -> list[str]:
    soup = BeautifulSoup(raw_text, "html.parser")
    text = soup.get_text("\n") if "<" in raw_text else raw_text
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines



def choose_score_section(lines):
    # Try to find a section that actually contains score patterns
    score_pattern = re.compile(r"[+-]\d+|E")

    best_section = []
    best_count = 0

    for i in range(len(lines)):
        window = lines[i:i+50]

        score_hits = sum(1 for line in window if score_pattern.search(line))

        if score_hits > best_count:
            best_count = score_hits
            best_section = window

    if best_count > 5:
        return best_section, "leaderboard"

    return lines, "unknown"



def find_player_line(section: Iterable[tuple[str, str]], aliases: list[str]) -> tuple[str, str] | None:
    normalized_aliases = [normalize_text(alias) for alias in aliases]
    for original_line, normalized_line in section:
        padded = f" {normalized_line} "
        for alias in normalized_aliases:
            if f" {alias} " in padded:
                return original_line, alias
    return None



def extract_line_after_alias(line: str, alias: str) -> str:
    normalized = normalize_text(line)
    idx = normalized.find(alias)
    if idx == -1:
        return normalized
    return normalized[idx + len(alias) :].strip()



def parse_completed_status(tokens: list[str], start_index: int, par: int) -> int | None:
    round_scores: list[int] = []
    total_strokes: int | None = None
    for token in tokens[start_index + 1 :]:
        if not re.fullmatch(r"\d+", token):
            continue
        value = int(token)
        if 50 <= value <= 90 and total_strokes is None:
            round_scores.append(value)
            continue
        if 100 <= value <= 400 and total_strokes is None:
            total_strokes = value
            break
    if total_strokes is None or not round_scores:
        return None
    return total_strokes - par * len(round_scores)



def derive_detail(tokens: list[str], score_index: int) -> str:
    trailing = tokens[score_index + 1 :]
    if "f" in trailing:
        return "Final"

    round_scores = [
        int(token)
        for token in trailing
        if re.fullmatch(r"\d+", token) and 50 <= int(token) <= 90
    ]
    totals = [
        int(token)
        for token in trailing
        if re.fullmatch(r"\d+", token) and 100 <= int(token) <= 400
    ]
    if len(round_scores) >= 4 and totals:
        return "Final"
    if len(round_scores) >= 2 and totals and not any(
        re.fullmatch(r"\d{1,2}", token) and 1 <= int(token) <= 18 for token in trailing
    ):
        return "Round complete"

    thru_candidates = [token for token in trailing if re.fullmatch(r"\d{1,2}", token) and 1 <= int(token) <= 18]
    if thru_candidates:
        return f"Thru {thru_candidates[0]}"

    if trailing:
        first = trailing[0].upper()
        if first in STATUS_TOKENS:
            return first
    return "Live"



def parse_player_state(line: str, alias: str, canonical_name: str, par: int) -> dict:
    tee_match = re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", line, re.IGNORECASE)
    if tee_match:
        tee_time = tee_match.group(0).upper()
        return {
            "name": canonical_name,
            "scoreToPar": 0,
            "scoreDisplay": "E",
            "status": "Tee time",
            "detail": f"Tee time {tee_time}",
            "teeTime": tee_time,
            "found": True,
            "sourceLine": line,
        }

    tail = extract_line_after_alias(line, alias)

    # ESPN sometimes concatenates SCORE and TODAY, e.g. "-3-3 6--------20"
    # Insert a space between adjacent signed score tokens so parsing can work.
    tail = re.sub(r"([+-]\d+)([+-]\d+)", r"\1 \2", tail)

    # ESPN also collapses trailing columns together with dashes.
    # Convert long dash runs to spaces so THRU / totals can be tokenized.
    tail = re.sub(r"-{2,}", " ", tail)

    cleaned_tail = re.sub(r"[^a-zA-Z0-9:+\-]+", " ", tail)
    tokens = [token for token in cleaned_tail.split() if token]

    score_index: int | None = None
    score_token: str | None = None
    for idx, token in enumerate(tokens):
        token_upper = token.upper()
        if token_upper == "E" or re.fullmatch(r"[+-]\d+", token_upper) or token_upper in STATUS_TOKENS:
            score_index = idx
            score_token = token_upper
            break

    if score_index is None or score_token is None:
        return {
            "name": canonical_name,
            "scoreToPar": 0,
            "scoreDisplay": "E",
            "status": "Not found",
            "detail": "Player row was not parsed.",
            "teeTime": None,
            "found": False,
            "sourceLine": line,
        }

    if score_token == "E":
        score = 0
        status = derive_detail(tokens, score_index)
        return {
            "name": canonical_name,
            "scoreToPar": score,
            "scoreDisplay": score_display(score),
            "status": status,
            "detail": status,
            "teeTime": None,
            "found": True,
            "sourceLine": line,
        }

    if re.fullmatch(r"[+-]\d+", score_token):
        score = int(score_token)
        status = derive_detail(tokens, score_index)
        return {
            "name": canonical_name,
            "scoreToPar": score,
            "scoreDisplay": score_display(score),
            "status": status,
            "detail": status,
            "teeTime": None,
            "found": True,
            "sourceLine": line,
        }

    computed_score = parse_completed_status(tokens, score_index, par)
    score = 0 if computed_score is None else computed_score
    return {
        "name": canonical_name,
        "scoreToPar": score,
        "scoreDisplay": score_display(score),
        "status": score_token,
        "detail": score_token,
        "teeTime": None,
        "found": True,
        "sourceLine": line,
    }



def load_config(config_path: Path) -> tuple[dict, list[TeamConfig]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    event = raw["event"]
    teams: list[TeamConfig] = []
    for team in raw["teams"]:
        players = [PlayerConfig(name=player["name"], aliases=player.get("aliases", [player["name"]])) for player in team["players"]]
        teams.append(
            TeamConfig(
                slug=team["slug"],
                display_name=team["displayName"],
                subtitle=team.get("subtitle", ""),
                players=players,
            )
        )
    return event, teams



def build_output(lines: list[str], mode: str, event: dict, teams: list[TeamConfig], source_url: str | None) -> dict:
    normalized_section = [(line, normalize_text(line)) for line in lines]
    rendered_teams: list[dict] = []

    for team in teams:
        rendered_players: list[dict] = []
        total = 0
        for player in team.players:
            found = find_player_line(normalized_section, [player.name, *player.aliases])
            if found is None:
                state = {
                    "name": player.name,
                    "scoreToPar": 0,
                    "scoreDisplay": "E",
                    "status": "Missing",
                    "detail": "Player was not found on the source page.",
                    "teeTime": None,
                    "found": False,
                    "sourceLine": None,
                }
            else:
                original_line, matched_alias = found
                state = parse_player_state(original_line, matched_alias, player.name, int(event["par"]))
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



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate landing-page JSON from the Masters leaderboard.")
    parser.add_argument("--config", default="teams.json", help="Path to teams.json")
    parser.add_argument("--out", default="site/data/latest.json", help="Output JSON path")
    parser.add_argument("--url", default=None, help="Leaderboard page URL")
    parser.add_argument("--input-file", default=None, help="Optional local HTML or text file for testing")
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    out_path = Path(args.out)
    input_file = Path(args.input_file) if args.input_file else None

    event, teams = load_config(config_path)
    source_url = args.url or event.get("leaderboardUrl")

    try:
        raw_text = read_source(source_url, input_file)
        Path("site/data/raw_source_debug.html").write_text(raw_text, encoding="utf-8")
        lines = html_to_lines(raw_text)
        section, mode = choose_score_section(lines)
        if mode == "tee-times":
            print("ERROR: Source returned tee times instead of live leaderboard data.", file=sys.stderr)
            return 1
        Path("site/data/raw_lines_debug.txt").write_text("\n".join(lines[:400]), encoding="utf-8")
        print(f"DEBUG mode={mode}")
        output = build_output(section, mode, event, teams, source_url)
    except Exception as exc:  # pragma: no cover - keeps workflow readable on failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
