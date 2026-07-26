"""Load World Cup match data and derive knockout-bracket structure from it.

Nothing here downloads anything -- it only reads worldcup_data/<year>.json
and works out, from the results themselves, which teams occupy the bracket's
entry round and in what order, which matches belong to which knockout stage,
and who won each one. Shared by WC_Brackets.py (the static renderer) and
build_web.py (the interactive page) so both use identical bracket geometry
and the same reconstruction logic.
"""

from __future__ import annotations

import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "worldcup_data"

# Every FIFA World Cup edition with a worldcup_data/<year>.json snapshot
# (1942/1946 were cancelled). Shared by build_web.py and WC_Brackets.py so
# the "which years exist" list lives in exactly one place.
ALL_YEARS = [
    1930, 1934, 1938, 1950, 1954, 1958, 1962, 1966, 1970, 1974, 1978, 1982,
    1986, 1990, 1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022, 2026,
]

# Canonical knockout-stage order, outermost (most entrants) to innermost.
STAGE_ORDER = ("R32", "R16", "QF", "SF", "F")
STAGE_LABEL = {
    "R32": "Round of 32",
    "R16": "Round of 16",
    "QF": "Quarter-final",
    "SF": "Semi-final",
    "F": "Final",
}
# Some early tournaments had no group stage at all: their first knockout
# round carries a generic name ("Preliminary round", "First round") that
# would otherwise be indistinguishable from a group-stage "First round"
# label used by later tournaments. Map those explicitly to Round of 16.
FIRST_ROUND_IS_R16 = {
    1934: "Preliminary round",
    1938: "First round",
}


def load_worldcup_json(source: str | Path) -> dict:
    """Load a downloaded OpenFootball World Cup JSON file."""
    source = Path(source)
    try:
        with source.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not load {source}: {error}") from error

    if not isinstance(data.get("matches"), list):
        raise RuntimeError(f"{source} does not contain a matches list")
    return data


def year_source(year: int) -> Path:
    """Path to read ``year``'s match data from."""
    return DATA_DIR / f"{year}.json"


def classify_round(year: int, round_name: object) -> str | None:
    """Map a raw ``round`` label to a canonical stage code (see STAGE_ORDER),
    or ``None`` if it isn't knockout-bracket stage (group/matchday/replay-less
    third-place matches are not part of the bracket)."""
    if not isinstance(round_name, str):
        return None

    override = FIRST_ROUND_IS_R16.get(year)
    if override and round_name.startswith(override):
        return "R16"

    normalized = round_name.strip().lower()
    if normalized == "final":
        return "F"
    if "round of 32" in normalized:
        return "R32"
    if "round of 16" in normalized:
        return "R16"
    if "quarter" in normalized:
        return "QF"
    if "semi" in normalized:
        return "SF"
    return None


def _winner_index(score: object) -> int | None:
    if not isinstance(score, dict):
        return None

    for score_type in ("p", "et", "ft"):
        values = score.get(score_type)
        if (
            isinstance(values, list)
            and len(values) == 2
            and all(isinstance(value, int) for value in values)
            and values[0] != values[1]
        ):
            return 0 if values[0] > values[1] else 1
    return None


def _match_winner(match: dict) -> str | None:
    """Winner of a single match, including pre-penalty-shootout-era walkovers
    (e.g. Austria's 1938 withdrawal after the Anschluss, recorded with no
    score and status "canceled" -- the listed team1 is the side that advanced)."""
    team1, team2 = match.get("team1"), match.get("team2")
    if match.get("status") == "canceled" and match.get("score") is None:
        return team1 if isinstance(team1, str) else None
    winner_index = _winner_index(match.get("score"))
    if winner_index is None:
        return None
    teams = (team1, team2)
    return teams[winner_index] if isinstance(teams[winner_index], str) else None


def _final_round_robin_decider(data: dict) -> list[dict]:
    """Fallback for the one World Cup (1950) with no knockout rounds at all:
    the championship was a round-robin "Final Round" group, so treat its
    chronologically last match as the de facto final (it was: Uruguay 2-1
    Brazil, the Maracanazo)."""
    candidates = [
        match
        for match in data["matches"]
        if isinstance(match.get("round"), str)
        and "final" in match["round"].strip().lower()
        and match["round"].strip().lower() != "final"
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda m: (m.get("date") or "", m.get("num") or 0))
    return [candidates[-1]]


def stage_matches(source: str | Path, year: int) -> dict[str, list[dict]]:
    """Group this year's matches by canonical knockout stage, in file order
    (so a later replay match naturally overrides an earlier drawn one)."""
    data = load_worldcup_json(source)
    grouped: dict[str, list[dict]] = {}
    for match in data["matches"]:
        stage = classify_round(year, match.get("round"))
        if stage is not None:
            grouped.setdefault(stage, []).append(match)

    if not grouped:
        fallback = _final_round_robin_decider(data)
        if fallback:
            grouped["F"] = fallback
    return grouped


def _dedup_last(matches: list[dict]) -> list[dict]:
    """Keep only the last-in-file-order match per team pair, so a replay
    (listed after the original drawn match it replayed) is the one used."""
    by_pair: dict[frozenset, dict] = {}
    for match in matches:
        team1, team2 = match.get("team1"), match.get("team2")
        if isinstance(team1, str) and isinstance(team2, str):
            by_pair[frozenset((team1, team2))] = match
    return list(by_pair.values())


def bracket_seed_order(source: str | Path, year: int) -> tuple[list[str], str]:
    """Return (entry-stage teams in TRUE bracket-adjacent order, stage code).

    Raw match order in the source data is roughly chronological, not bracket
    seed order: two Round-of-16 winners who meet in the Quarter-final are not
    necessarily adjacent in the file (e.g. 1938: Sweden's real Quarter-final
    opponent was Cuba, not the team next to them in the Round-of-16 list).
    Naively pairing by file-order position therefore reconstructs a bracket
    tree that doesn't match history and often never reaches a champion.

    Instead this works backward from the Final: each round's real winner is
    matched to the two teams of the match they won in the round before,
    recursively down to the entry stage, so adjacency is derived from the
    results themselves rather than assumed from file order.
    """
    grouped = stage_matches(source, year)
    stages_present = [stage for stage in STAGE_ORDER if grouped.get(stage)]
    if not stages_present:
        raise RuntimeError(f"No knockout-stage matches found for {year}")

    # winner_at[stage] = {winner_name: (team1, team2)}, one stage at a time
    # (a team can win a match at more than one stage over a tournament, so
    # this must not be flattened into a single team-name-keyed dict).
    winner_at: dict[str, dict[str, tuple[str, str]]] = {}
    for stage in stages_present:
        stage_winners: dict[str, tuple[str, str]] = {}
        for match in _dedup_last(grouped[stage]):
            winner = _match_winner(match)
            if winner:
                stage_winners[winner] = (match.get("team1"), match.get("team2"))
        winner_at[stage] = stage_winners

    def expand(team: str, stage_index: int) -> list[str]:
        if stage_index <= 0:
            return [team]  # already at the entry stage: this is a leaf
        pair = winner_at[stages_present[stage_index - 1]].get(team)
        if pair is None:
            return [team]  # incomplete data: degrade to a leaf, don't crash
        team1, team2 = pair
        return expand(team1, stage_index - 1) + expand(team2, stage_index - 1)

    final_stage_index = len(stages_present) - 1
    final_match = _dedup_last(grouped[stages_present[final_stage_index]])[0]
    team1, team2 = final_match.get("team1"), final_match.get("team2")
    teams = expand(team1, final_stage_index) + expand(team2, final_stage_index)
    return teams, stages_present[0]


def entry_stage_teams(
    source: str | Path, year: int
) -> tuple[list[str], str]:
    """Return (teams in true bracket-adjacent order, stage code) for the
    bracket's outermost (entry) round -- the earliest stage in STAGE_ORDER
    with any matches. See bracket_seed_order() for why this isn't simply the
    entry stage's matches in raw file order."""
    return bracket_seed_order(source, year)


# The champion badge (the year badge or, for 2026, the tournament logo) is
# drawn as a circle of this radius at the centre -- see CHAMPION_BADGE_RADIUS
# usages in WC_Brackets.py and build_web.py. MIN_CENTER_RADIUS is how close
# the innermost ring (where the two finalists sit just before merging into
# the champion) is allowed to get to that badge; without a floor, brackets
# with few rounds (e.g. an 8-team QF-start bracket) would place the
# finalists' ring almost flush against the badge, since the ring spacing
# was previously computed the same way regardless of how much of it the
# centre badge eats into.
CHAMPION_BADGE_RADIUS = 0.22
MIN_CENTER_RADIUS = 0.37  # clearance beyond the champion badge


def bracket_radii(n_teams: int) -> list[float]:
    """Ring radii from the outer leaves to the champion at the centre.
    The outer rings are evenly spaced from 1.0 down to MIN_CENTER_RADIUS
    (the innermost, finalists' ring), then a final gap collapses that down
    to 0.0 for the champion. Generalizes to any power-of-two bracket size;
    shared by WC_Brackets.py and build_web.py so the static and interactive
    brackets use identical geometry."""
    n_rounds = n_teams.bit_length() - 1  # log2(n_teams): halvings to reach 1
    if n_rounds <= 1:
        return [1.0, 0.0]
    span = 1.0 - MIN_CENTER_RADIUS
    step = span / (n_rounds - 1)
    return [round(1.0 - i * step, 4) for i in range(n_rounds)] + [0.0]


def print_entry_stage_teams(source: str | Path, year: int) -> list[str]:
    """Print and return the bracket's entry-stage team list."""
    teams, stage = entry_stage_teams(source, year)
    print(f"{STAGE_LABEL.get(stage, stage)} teams ({year}):")
    for index, team in enumerate(teams, start=1):
        print(f"{index:2}. {team}")
    return teams


def extract_knockout_results(source: str | Path, year: int) -> list[dict]:
    """Return normalized knockout matches and their winner when decided."""
    results = []
    for stage, matches in stage_matches(source, year).items():
        for match in matches:
            results.append(
                {
                    "round": match.get("round"),
                    "stage": stage,
                    "match_number": match.get("num"),
                    "team1": match.get("team1"),
                    "team2": match.get("team2"),
                    "score": match.get("score"),
                    "winner": _match_winner(match),
                }
            )
    return results


def extract_knockout_winners(source: str | Path, year: int) -> dict[frozenset[str], str]:
    """Return graph-ready ``{matchup: winner}`` entries for decided games.

    Matches are processed in file order, so a replay (listed after the
    original drawn match, e.g. 1934/1938's "..., Replays" rounds) naturally
    overwrites that matchup's entry with the replay's result.
    """
    winners: dict[frozenset[str], str] = {}
    for result in extract_knockout_results(source, year):
        team1, team2, winner = result["team1"], result["team2"], result["winner"]
        if (
            isinstance(team1, str)
            and isinstance(team2, str)
            and isinstance(winner, str)
            and not (team1[:1] in ("W", "L") and team1[1:].isdigit())
            and not (team2[:1] in ("W", "L") and team2[1:].isdigit())
        ):
            winners[frozenset((team1, team2))] = winner
    return winners
