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


def make_short_alias(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def read_source(url: str | None, input_file: Path | None) -> str:
    if input_file is not None:
        return input_file.read_text(encoding="utf-8")

    if not url:
        raise ValueError("A URL is required when --input-file is not provided.")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 2400},
            )
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(10000)
            text = page.locator("body").inner_text()
            browser.close()
            return text
    except Exception:
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
    if re.search(r"\b(?:F|\d{1,2}:\d{2}\s*(?:AM|PM)|\d{1,2})\b", line):
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

        i += 1

    return rows


def find_player_row(rows: list[tuple[str, str]], aliases: list[str]) -> tuple[str, str] | None:
    normalized_aliases = [normalize_text(a) for a in aliases]
    for original_row, normalized_row in rows:
        padded = f" {normalized_row} "
        for alias in normalized_aliases:
            if f" {alias} " in padded:
                return original_row, alias
    return None


def extract_tail_after_alias(row: str, alias: str) -> str:
    normalized = normalize_text(row)
    idx = normalized.find(alias)
    if idx == -1:
        return normalized
    return normalized[idx + len(alias):].strip()


def derive_detail(tokens: list[str], score_index: int) -> str:
    trailing = tokens[score_index + 1 :]

    if not trailing:
        return "Live"

    upper_trailing = [t.upper() for t in trailing]

    if "F" in upper_trailing:
        return "Final"

    for token in trailing:
        if re.fullmatch(r"\d{1,2}", token):
            value = int(token)
            if 1 <= value <= 18:
                return f"Thru {value}"

    if upper_trailing[0] in STATUS_TOKENS:
        return upper_trailing[0]

    if re.fullmatch(r"\d{1,2}:\d{2}", trailing[0]) and len(trailing) > 1:
        ampm = trailing[1].upper()
        if ampm in {"AM", "PM"}:
            return f"Tee time {trailing[0]} {ampm}"

    return "Live"


def parse_player_row(row: str, alias: str, canonical_name: str) -> dict:
    tail = extract_tail_after_alias(row, alias)
    cleaned_tail = re.sub(r"[^a-zA-Z0-9:+\-]+", " ", tail)
    tokens = [token for token in cleaned_tail.split() if token]

    if not tokens:
        return {
            "name": canonical_name,
            "scoreToPar": 0,
            "scoreDisplay": "E",
            "status": "Not found",
            "detail": "Player row was not parsed.",
            "teeTime": None,
            "found": False,
            "sourceLine": row,
        }

    # tee time rows look like "- - 5:44 PM -- -- -- -- --"
    if len(tokens) >= 4 and tokens[0] == "-" and tokens[1] == "-" and re.fullmatch(r"\d{1,2}:\d{2}", tokens[2]):
        tee_time = f"{tokens[2]} {tokens[3].upper()}"
        return {
            "name": canonical_name,
            "scoreToPar": 0,
            "scoreDisplay": "E",
            "status": "Tee time",
            "detail": f"Tee time {tee_time}",
            "teeTime": tee_time,
            "found": True,
            "sourceLine": row,
        }

    score_index = None
    score_token = None

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
            "sourceLine": row,
        }

    if score_token == "E":
        score = 0
    elif re.fullmatch(r"[+-]\d+", score_token):
        score = int(score_token)
    else:
        score = 0

    detail = derive_detail(tokens, score_index)

    return {
        "name": canonical_name,
        "scoreToPar": score,
        "scoreDisplay": score_display(score),
        "status": detail,
        "detail": detail,
        "teeTime": None,
        "found": True,
        "sourceLine": row,
    }


def load_config(config_path: Path) -> tuple[dict, list[TeamConfig]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    event = raw["event"]
    teams: list[TeamConfig] = []

    for team in raw["teams"]:
        players = []
        for player in team["players"]:
            name = player["name"]
            aliases = player.get("aliases", [name])

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


def build_output(lines: list[str], mode: str, event: dict, teams: list[TeamConfig], source_url: str | None) -> dict:
    leaderboard_rows = build_leaderboard_rows(lines)
    normalized_rows = [(row, normalize_text(row)) for row in leaderboard_rows]
    rendered_teams: list[dict] = []

    for team in teams:
        rendered_players: list[dict] = []
        total = 0

        for player in team.players:
            found = find_player_row(normalized_rows, [player.name, *player.aliases])

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
                original_row, matched_alias = found
                state = parse_player_row(original_row, matched_alias, player.name)

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

    raw_text = read_source(source_url, input_file)
    lines = html_to_lines(raw_text)
    section, mode = choose_score_section(lines)

    debug_dir = Path("site/data")
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "raw_lines_debug.txt").write_text("\n".join(lines[:800]), encoding="utf-8")
    (debug_dir / "section_debug.txt").write_text("\n".join(section[:250]), encoding="utf-8")
    (debug_dir / "rows_debug.txt").write_text("\n".join(build_leaderboard_rows(section)), encoding="utf-8")

    output = build_output(section, mode, event, teams, source_url)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
