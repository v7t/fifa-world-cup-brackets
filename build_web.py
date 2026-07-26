"""Build an interactive web page for the FIFA World Cup knockout bracket,
covering every edition from 1930 to 2026.

Writes a single self-contained HTML page containing:
  * a year list (left) -- selecting one switches the whole page to that
    edition's bracket, results and match details
  * the circular bracket, drawn as vector SVG in the browser
  * a slider (bottom) from 0 games played up to the number of decided games
  * a results list and a match-detail column (right) that reveal matches as
    the slider advances

Every flag/crest/logo is embedded once (shared across all 23 editions) as an
SVG data-URI, and the browser re-derives the bracket for any slider position,
so scrubbing is instant.

    python build_web.py                 # -> docs/index.html (served by GitHub Pages)
    python build_web.py out/page.html   # -> a path of your choosing

Match loading and bracket reconstruction live in bracket_data.py, asset
filename conventions in asset_paths.py, and team/flag/crest metadata in
historical_teams.py; download_resources.py fetches everything this script
reads from disk.
"""

import os
import re
import json
import base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import historical_teams
from asset_paths import crest_filename, svg_aspect
from bracket_data import (
    ALL_YEARS,
    bracket_radii,
    entry_stage_teams,
    extract_knockout_winners,
    load_worldcup_json,
    stage_matches,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAG_SVG_DIR = os.path.join(SCRIPT_DIR, "country_flags")
CREST_DIR = os.path.join(SCRIPT_DIR, "country_crests")
LOGO_DIR = os.path.join(SCRIPT_DIR, "tournament_logos")
DATA_DIR = os.path.join(SCRIPT_DIR, "worldcup_data")
HIGHLIGHTS_PATH = os.path.join(SCRIPT_DIR, "highlights_data", "2026_worldcup_highlights.json")
# Default output is docs/index.html: that is what GitHub Pages serves
# (Settings -> Pages -> Deploy from a branch -> master -> /docs).
OUT_PATH = os.path.join(SCRIPT_DIR, "docs", "index.html")

STAGE_DISPLAY = {
    "R32": "Round of 32", "R16": "Round of 16", "QF": "Quarter Finals",
    "SF": "Semi Finals", "F": "Final",
}


def year_source_path(year):
    return os.path.join(DATA_DIR, f"{year}.json")


def year_logo_path(year):
    return os.path.join(LOGO_DIR, f"{year}_worldcup_logo.svg")


def _display_name(name):
    """Outer-ring label for a team name: wraps long names onto multiple
    lines (or applies an explicit override, e.g. "USA" -> the full name
    split across two lines) -- see historical_teams.display_name."""
    return historical_teams.display_name(name)



# Image sizes are taken from matplotlib so the page matches the PNG/SVG output.
# Each flag/crest is an OffsetImage of  native_px * zoom * (dpi/72)  display
# pixels; convert that through the axes transform to get data units.
FLAG_PX, FLAG_ZOOM, CREST_ZOOM = 240, 0.155, 0.190

# Champion badge: a plain top-to-bottom stack at the centre -- the
# tournament logo, then the winner's flag below it, then the winner's name
# (one word per line, for multi-word names), then the subtitle -- rather
# than overlapping the flag on top of the logo. Matches WC_Brackets.py.
CHAMP_ZOOM = FLAG_ZOOM   # same size as every other flag
LOGO_CENTER_Y = 0.09     # tournament logo/year badge, shifted up from centre
LOGO_BG_SIZE = 0.40      # solid background square behind the logo (covers
                         # every edition's logo -- all are normalized to the
                         # same ~0.38 max dimension, see LOGO_RASTER_MAX_DIM)
CHAMP_FLAG_Y = -0.21     # winner's flag, below the logo (leaves a visible
                         # gap below the logo's background square for the
                         # connecting white line -- don't let this creep up
                         # to where the halo would touch that square)
CHAMP_TEXT_Y = -0.30     # winner's name, below the flag (first word/line)
CHAMP_TEXT_Y_NOLOGO = -0.30   # fallback path is now identical (logo is
                              # basically always present; kept distinct in
                              # case that ever isn't true again)
CHAMP_NAME_LINE_DY = -0.05    # spacing between stacked name lines, and from
                              # the (possibly multi-line) name to the subtitle
CHAMP_NAME_FS = 16      # nation: large and bold   (matplotlib points)
CHAMP_SUB_FS = 9        # subtitle: small, regular weight
CHAMP_SUBTITLE = "World Cup Champions"


# Tournament logos vary wildly in native shape (1930's is tall and narrow,
# 1986's is wide); rasterizing every logo so its LARGER dimension is this
# many pixels keeps them all a consistent size on the page regardless of
# aspect ratio (matches WC_Brackets.py's LOGO_RASTER_MAX_DIM).
LOGO_RASTER_MAX_DIM = 1235


def logo_wh(aspect, dpi_cor, to_data):
    """(logoW, logoH) in data units for a logo of this aspect ratio (w/h),
    sized so its larger dimension is LOGO_RASTER_MAX_DIM pixels -- see
    LOGO_RASTER_MAX_DIM."""
    width_px = LOGO_RASTER_MAX_DIM if aspect >= 1 else LOGO_RASTER_MAX_DIM * aspect
    height_px = width_px / aspect
    return to_data(width_px * 0.11 * dpi_cor), to_data(height_px * 0.11 * dpi_cor)


def layout_sizes():
    """(sizes, dpi_cor, to_data): flag/crest/champion sizes in data units as
    matplotlib renders them (constant across editions -- `sizes` is JSON-safe
    and goes straight into the payload), plus the raw dpi_cor/to_data used to
    size each edition's own logo via logo_wh (every edition's logo has its
    own aspect ratio, so that can't be precomputed once like the others)."""
    fig, ax = plt.subplots(figsize=(16, 16))
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.6, 1.6)
    ax.set_aspect("equal")
    ax.axis("off")
    plt.tight_layout()
    fig.canvas.draw()
    dpi_cor = fig.dpi / 72.0
    inv = ax.transData.inverted()

    def to_data(px):
        return abs(inv.transform((px, 0))[0] - inv.transform((0, 0))[0])

    sizes = {
        "flagD": to_data(FLAG_PX * FLAG_ZOOM * dpi_cor),
        "crestD": to_data(260 * CREST_ZOOM * dpi_cor),
        "champD": to_data(FLAG_PX * CHAMP_ZOOM * dpi_cor),
    }
    plt.close(fig)
    return sizes, dpi_cor, to_data


def data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/svg+xml;base64," + base64.b64encode(fh.read()).decode("ascii")


def score_text(score, status=None):
    if status == "canceled":
        return "w/o"  # walkover: e.g. Austria's withdrawal, 1938
    if not isinstance(score, dict):
        return ""
    ft, et, p = score.get("ft"), score.get("et"), score.get("p")
    txt = f"{ft[0]}-{ft[1]}" if isinstance(ft, list) and len(ft) == 2 else ""
    if isinstance(et, list) and len(et) == 2:
        txt = f"{et[0]}-{et[1]} aet"
    if isinstance(p, list) and len(p) == 2:
        txt += f" (pens {p[0]}-{p[1]})"
    return txt


def load_highlights():
    """match number -> {'highlights': {...}, 'extended': {...}} video links.

    Optional: the page degrades gracefully to 'no video' if the file is absent.
    Keyed on match_number, which lines up exactly with worldcup.json's 'num'.
    """
    if not os.path.exists(HIGHLIGHTS_PATH):
        print(f"[warn] {os.path.basename(HIGHLIGHTS_PATH)} not found; no video links.")
        return {}, ""
    with open(HIGHLIGHTS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    by_num = {}
    for m in data.get("matches", []):
        num = m.get("match_number")
        if num is None:
            continue
        entry = {}
        for src, dst in (("highlights", "hl"), ("extended_highlights", "ext")):
            v = m.get(src)
            if isinstance(v, dict) and v.get("url"):
                vid = re.search(r"[?&]v=([\w-]+)", v["url"])
                entry[dst] = {"title": v.get("title", ""), "url": v["url"],
                              "id": vid.group(1) if vid else ""}
        if entry:
            by_num[num] = entry
    return by_num, data.get("source_channel", "")


def _goals(lst, code):
    """Normalise a worldcup.json goals list for one team into render-ready rows."""
    out = []
    for g in lst or []:
        out.append({"code": code, "name": g.get("name", ""),
                    "min": str(g.get("minute", "")),
                    "pen": bool(g.get("penalty")), "og": bool(g.get("owngoal"))})
    return out


def _minute_key(g):
    """Sort key for a goal minute like '45', '90+2', '120+5'."""
    m = re.match(r"(\d+)(?:\+(\d+))?", g["min"])
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def _group_label(m):
    """Which group-stage table a match belongs to, or None for a knockout/
    matchday-only entry. Most matches carry an explicit 'group' field, but
    two irregular cases don't:
      - a group play-off/decider (e.g. 1958's "Group 1 Play-off") has no
        'group' field, so it's derived from the round name's own prefix.
      - 1950's round-robin championship match ("Final Round") also has no
        'group' field and no "Group X" round prefix, so it's special-cased.
    """
    g = m.get("group")
    if g:
        return g
    raw = m.get("round") or ""
    prefix = re.match(r"^(Group\s+\S+)", raw)
    if prefix:
        return prefix.group(1)
    if "final round" in raw.lower():
        return "Final Round"
    return None


def build_group_tables(source, year):
    """Standings + match list for every group in this edition's group stage.

    Numbered groups ("Group 1".."Group N") are the first round; lettered
    groups ("Group A"..), present only in 1974/1978/1982, are the second-round
    group stage those editions used instead of a quarter-final bracket. Tables
    are returned in the order their group first appears in the data, so first-
    and second-round groups (and 1950's "Final Round") sort correctly without
    needing special-case ordering logic.
    """
    data = load_worldcup_json(source)
    points_per_win = 3 if year >= 1994 else 2  # WC used 2 pts/win before 1994

    order = []
    by_group = {}
    for m in data["matches"]:
        label = _group_label(m)
        if label is None:
            continue
        by_group.setdefault(label, []).append(m)
        if label not in order:
            order.append(label)

    tables = []
    for label in order:
        stats = {}
        rows = []
        for m in by_group[label]:
            team1, team2 = m.get("team1"), m.get("team2")
            if not isinstance(team1, str) or not isinstance(team2, str):
                continue
            a, b = historical_teams.flag_code(team1), historical_teams.flag_code(team2)
            if not a or not b:
                continue
            goals = _goals(m.get("goals1"), a) + _goals(m.get("goals2"), b)
            goals.sort(key=_minute_key)
            ft = (m.get("score") or {}).get("ft")
            winner_code = None
            if isinstance(ft, list) and len(ft) == 2:
                if ft[0] > ft[1]:
                    winner_code = a
                elif ft[1] > ft[0]:
                    winner_code = b
            rows.append({
                "round": label, "date": m.get("date", ""), "a": a, "b": b,
                "score": score_text(m.get("score"), m.get("status")),
                "ground": m.get("ground", ""), "goals": goals, "winner": winner_code,
            })

            if not (isinstance(ft, list) and len(ft) == 2):
                continue           # not played, or a walkover with no scoreline
            s1, s2 = ft
            for name in (team1, team2):
                stats.setdefault(name, {"played": 0, "won": 0, "drawn": 0,
                                         "lost": 0, "gf": 0, "ga": 0, "pts": 0})
            stats[team1]["played"] += 1
            stats[team2]["played"] += 1
            stats[team1]["gf"] += s1
            stats[team1]["ga"] += s2
            stats[team2]["gf"] += s2
            stats[team2]["ga"] += s1
            if s1 > s2:
                stats[team1]["won"] += 1
                stats[team1]["pts"] += points_per_win
                stats[team2]["lost"] += 1
            elif s2 > s1:
                stats[team2]["won"] += 1
                stats[team2]["pts"] += points_per_win
                stats[team1]["lost"] += 1
            else:
                stats[team1]["drawn"] += 1
                stats[team1]["pts"] += 1
                stats[team2]["drawn"] += 1
                stats[team2]["pts"] += 1

        standings = []
        for name, s in stats.items():
            code = historical_teams.flag_code(name)
            if not code:
                continue
            standings.append({
                "code": code, "name": name, "gd": s["gf"] - s["ga"], **s,
            })
        # points, then goal difference, then goals for -- the modern
        # tie-break order; historical goal-average-era ties are rare enough
        # that this is a fine approximation for display purposes.
        standings.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"], r["name"]))
        rows.sort(key=lambda r: r["date"])
        tables.append({"label": label, "standings": standings, "matches": rows})
    return tables


def _match_row(m, label, winners_by_pair, videos):
    team1, team2 = m.get("team1"), m.get("team2")
    if not isinstance(team1, str) or not isinstance(team2, str):
        return None
    a, b = historical_teams.flag_code(team1), historical_teams.flag_code(team2)
    winner_name = winners_by_pair.get(frozenset((team1, team2)))
    winner_code = historical_teams.flag_code(winner_name) if winner_name else None
    if not a or not b or not winner_code:
        return None                       # undecided, or a "W73"-style placeholder
    num = m.get("num") or 0
    goals = _goals(m.get("goals1"), a) + _goals(m.get("goals2"), b)
    goals.sort(key=_minute_key)
    return {
        "round": label,
        "date": m.get("date", ""),
        "a": a, "b": b,
        "winner": winner_code,
        "score": score_text(m.get("score"), m.get("status")),
        "num": num,
        "ground": m.get("ground", ""),
        "goals": goals,
        "video": videos.get(num, {}),
    }


def _decisive_winner_index(score):
    """Winner index (0/1), checking penalties then extra time then full time
    -- mirrors bracket_data._winner_index. Third-place matches aren't
    part of the bracket, so they're not in extract_knockout_winners()'s
    replay/walkover-aware map; this determines their winner independently."""
    if not isinstance(score, dict):
        return None
    for key in ("p", "et", "ft"):
        values = score.get(key)
        if (isinstance(values, list) and len(values) == 2
                and all(isinstance(v, int) for v in values) and values[0] != values[1]):
            return 0 if values[0] > values[1] else 1
    return None


def _third_place_row(m, videos):
    team1, team2 = m.get("team1"), m.get("team2")
    if not isinstance(team1, str) or not isinstance(team2, str):
        return None
    a, b = historical_teams.flag_code(team1), historical_teams.flag_code(team2)
    if not a or not b:
        return None
    wi = _decisive_winner_index(m.get("score"))
    if wi is None:
        return None
    num = m.get("num") or 0
    goals = _goals(m.get("goals1"), a) + _goals(m.get("goals2"), b)
    goals.sort(key=_minute_key)
    return {
        "round": "Third Place", "date": m.get("date", ""),
        "a": a, "b": b, "winner": (a, b)[wi],
        "score": score_text(m.get("score"), m.get("status")),
        "num": num, "ground": m.get("ground", ""), "goals": goals,
        "video": videos.get(num, {}),
    }


def collect_matches(source, year, videos):
    """Decided knockout matches for one edition, in chronological order, with
    video links (2026 only -- the highlights file is keyed to that year's
    match numbers).

    Built from stage_matches(), which already folds in the two irregular
    cases: a replay match (1934/1938) shares its stage bucket with the
    original drawn match it replayed, and 1950 (no labeled knockout rounds at
    all) gets its round-robin decider synthesized under stage "F". Third-place
    matches have no bracket stage at all, so they're gathered (and their
    winner determined) separately.
    """
    winners_by_pair = extract_knockout_winners(source, year)  # replay/walkover-aware

    rows = []
    for stage, matches in stage_matches(source, year).items():
        # keep only the last match per team-pair within a stage, so a replay
        # (listed after the original in file order) is what actually shows
        by_pair = {}
        for m in matches:
            team1, team2 = m.get("team1"), m.get("team2")
            if isinstance(team1, str) and isinstance(team2, str):
                by_pair[frozenset((team1, team2))] = m
        for m in by_pair.values():
            row = _match_row(m, STAGE_DISPLAY[stage], winners_by_pair, videos)
            if row:
                rows.append(row)

    data = load_worldcup_json(source)
    for m in data["matches"]:
        raw_round = m.get("round")
        if isinstance(raw_round, str) and "third" in raw_round.lower():
            row = _third_place_row(m, videos)
            if row:
                rows.append(row)

    rows.sort(key=lambda r: (r["date"], r["num"]))
    return rows


def build_year_payload(year, videos, source_channel, dpi_cor, to_data):
    source = year_source_path(year)
    names, _stage = entry_stage_teams(source, year)
    teams = [
        {
            "name": _display_name(n),
            "rawName": n,
            "code": historical_teams.flag_code(n),
            "acronym": historical_teams.acronym(n),
        }
        for n in names
    ]
    n_teams = len(teams)
    has_highlights = year == 2026  # FOX Sports' YouTube catalog only covers 2026
    matches = collect_matches(source, year, videos if has_highlights else {})

    logo_path = year_logo_path(year)
    has_logo = os.path.exists(logo_path)
    logo_w = logo_h = None
    if has_logo:
        logo_w, logo_h = logo_wh(svg_aspect(logo_path), dpi_cor, to_data)

    host = [
        {"name": n, "code": historical_teams.flag_code(n)}
        for n in historical_teams.host_countries(year)
    ]

    return {
        "year": year,
        "host": host,
        "teams": teams,
        "matches": matches,
        "groups": build_group_tables(source, year),
        "radii": bracket_radii(n_teams),
        "startAngleDeg": 90 - (360 / n_teams) / 2,
        "angleStepDeg": 360 / n_teams,
        "hasLogo": has_logo,
        "logo": data_uri(logo_path) if has_logo else None,
        "logoW": logo_w, "logoH": logo_h,
        "champTextY": CHAMP_TEXT_Y if has_logo else CHAMP_TEXT_Y_NOLOGO,
        "sourceChannel": source_channel if has_highlights else "",
    }


def build_payload():
    videos, source_channel = load_highlights()
    sizes, dpi_cor, to_data = layout_sizes()

    by_year = {}
    for year in ALL_YEARS:
        by_year[str(year)] = build_year_payload(year, videos, source_channel, dpi_cor, to_data)
        yp = by_year[str(year)]
        n_vid = sum(1 for m in yp["matches"] if m["video"])
        print(f"  {year}: {len(yp['teams'])} teams, {len(yp['matches'])} matches"
              + (f", {n_vid} with video" if yp["hasLogo"] else ""))

    # Shared assets: the union of every team that appears in any edition's
    # bracket OR group table (group rosters include teams knocked out before
    # the bracket's entry round, e.g. East Germany in 1974's Group 1), so
    # flags/crests are embedded exactly once regardless of how many of the 23
    # tournaments reuse them.
    #
    # Flags are shared per flag CODE: a defunct entity and its modern
    # successor genuinely share the same real flag (Czechoslovakia and the
    # Czech Republic, West Germany and Germany). Crests are shared per EXACT
    # team NAME instead -- collapsing them by code would show, say, today's
    # Germany crest for West Germany just because both map to code "de",
    # which is wrong: those are different federation badges (where we have
    # both on file; otherwise it falls back to the generated shield badge,
    # which is still preferable to silently borrowing the wrong one).
    all_codes = set()
    all_names = set()
    for year in ALL_YEARS:
        yp = by_year[str(year)]
        names, _stage = entry_stage_teams(year_source_path(year), year)
        source_names = list(names)
        for table in yp["groups"]:
            source_names.extend(s["name"] for s in table["standings"])
        for name in source_names:
            code = historical_teams.flag_code(name)
            if code:
                all_codes.add(code)
                all_names.add(name)
        for name in historical_teams.host_countries(year):
            code = historical_teams.flag_code(name)
            if code:
                all_codes.add(code)

    flags, crests, aspects = {}, {}, {}
    for code in sorted(all_codes):
        svg_path = os.path.join(FLAG_SVG_DIR, f"{code}.svg")
        if os.path.exists(svg_path):
            flags[code] = data_uri(svg_path)
    for name in sorted(all_names):
        crest_path = os.path.join(CREST_DIR, crest_filename(historical_teams.crest_source_name(name)))
        if os.path.exists(crest_path):
            crests[name] = data_uri(crest_path)
            aspects[name] = svg_aspect(crest_path)

    payload = {
        "canvas": 1600, "view": 1.6, "fs": 15,
        "champFlagY": CHAMP_FLAG_Y, "logoCenterY": LOGO_CENTER_Y, "logoBgSize": LOGO_BG_SIZE,
        "champNameLineDy": CHAMP_NAME_LINE_DY, "champSubtitle": CHAMP_SUBTITLE,
        # matplotlib point sizes -> SVG user units (team names: 9 pt drawn at fs 15)
        "champNameFs": CHAMP_NAME_FS * 15 / 9, "champSubFs": CHAMP_SUB_FS * 15 / 9,
        "colors": {"bg": "#0a0a0a", "line": "#5a5a5a", "text": "#f2f2f2",
                   "gold": "#e6c35c", "white": "#ffffff"},
        "years": list(reversed(ALL_YEARS)),
        "byYear": by_year,
        "flags": flags, "crests": crests, "crestAspect": aspects,
    }
    payload.update(sizes)
    return payload


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FIFA World Cup — Knockout Bracket</title>
<style>
  /* Whole-page font scale: 175% of the 16px default -> 1rem = 28px.  Every
     text size below is in rem, so it tracks this one knob.  (The bracket SVG
     uses its own font sizes in user units and is unaffected.) */
  :root { font-size: 175%; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #0a0a0a; color: #f2f2f2;
    height: 100vh; height: 100dvh;   /* dvh: excludes the mobile browser's address bar */
    display: flex; flex-direction: column; overflow-x: auto; overflow-y: hidden;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  /* Desktop only (see the mobile breakpoint below, which switches to a
     stacked layout instead): below ~1900px -- the sum of the fixed-width
     side columns plus a legible minimum for the bracket -- the row no
     longer fits, so it keeps its natural width instead of crushing
     .stage-col, and the body scrolls horizontally rather than letting the
     crushed column's content overlap its neighbours. */
  .main { flex: 1; display: flex; min-height: 0; min-width: 1900px; }

  /* left-most column: every edition, 1930-2026; selecting one swaps the
     bracket, results and match details to that tournament */
  .years {
    width: 265px; flex-shrink: 0; border-right: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .years h2 {
    margin: 0; padding: .5rem .6rem .38rem; font-size: .82rem; letter-spacing: .12em;
    text-transform: uppercase; color: #9a9a9a; border-bottom: 1px solid #242424;
  }
  .year-list {
    overflow-y: auto; flex: 1; padding: .35rem; scrollbar-width: none; -ms-overflow-style: none;
  }
  .year-list::-webkit-scrollbar { width: 0; height: 0; }
  .year-row {
    padding: .4rem .5rem; border-radius: 6px; cursor: pointer; font-size: .88rem;
    color: #b9b9b9; border: 1px solid transparent; margin-bottom: .1rem;
  }
  .year-row:hover { background: #171717; color: #f2f2f2; }
  .year-row.active { background: #1c1c1c; border-color: #e6c35c; color: #e6c35c; font-weight: 600; }
  .year-row .yr-top { display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; }
  .year-row .yr-num { white-space: nowrap; }
  .year-row .yr-flag {
    width: 1em; height: 1em; border-radius: 50%; object-fit: cover; flex-shrink: 0;
    box-shadow: 0 0 0 1px rgba(255,255,255,.25);
  }
  .year-row .yr-host {
    margin-top: .15rem; font-size: .76rem; line-height: 1.2; color: #8a8a8a;
    overflow-wrap: break-word;
  }
  .year-row.active .yr-host { color: #c9a94a; }

  .stage-col { flex: 1; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
  .view-tabs { display: flex; gap: .4rem; padding: .5rem .5rem 0; }
  .view-tabs:empty { padding: 0; }
  .tab {
    background: #1a1a1a; color: #b9b9b9; border: 1px solid #333; border-radius: 6px;
    padding: .3rem .75rem; font-size: .78rem; cursor: pointer;
  }
  .tab:hover { background: #232323; color: #f2f2f2; }
  .tab.active { background: #1c1c1c; border-color: #e6c35c; color: #e6c35c; }
  .tab.mobile-only { display: none; }

  .stage {
    flex: 1; min-width: 0; display: flex; align-items: center;
    justify-content: center; padding: 8px; min-height: 0;
  }
  .stage svg {
    width: 100%; height: 100%; max-height: 100%;
    touch-action: none;   /* so dragging a finger across the bracket doesn't scroll/zoom the page */
    /* Without these, a press-and-hold on a team-name <text> label triggers the
       browser's own text-selection/"Search Google for..." long-press gesture
       instead of (or as well as) our touchstart handler below. Scoped to the
       SVG itself, not all of .stage -- Groups view lives in .stage too, and
       its cards need to stay normally scrollable/selectable. */
    -webkit-touch-callout: none;
    -webkit-user-select: none; user-select: none;
  }

  /* Circular magnifier: touching the bracket clones its current <svg> into
     here at a larger size, then just repositions/pans that clone as the
     finger moves -- re-cloning on every touchmove would be wasteful, and
     the bracket's own content only changes between touches anyway. */
  .magnifier {
    display: none; position: fixed; width: 255px; height: 255px; border-radius: 50%;
    border: 3px solid #e6c35c; box-shadow: 0 6px 20px rgba(0,0,0,.7);
    overflow: hidden; z-index: 200; pointer-events: none; background: #0a0a0a;
  }
  .magnifier.active { display: block; }
  .magnifier-inner { position: absolute; }
  .magnifier-inner svg { display: block; width: 100%; height: 100%; }

  /* Groups view: a wrapping grid of standings + results cards, replacing the
     bracket SVG in the same stage area. */
  .groups-view {
    width: 100%; height: 100%; overflow-y: auto; align-content: flex-start;
    display: flex; flex-wrap: wrap; gap: .8rem; padding: .3rem;
    scrollbar-width: none; -ms-overflow-style: none;
  }
  .groups-view::-webkit-scrollbar { width: 0; height: 0; }
  .gt-card {
    width: 460px; flex-shrink: 0; background: #131313; border: 1px solid #242424;
    border-radius: 10px; padding: .7rem .8rem; overflow: hidden;
  }
  .gt-card h3 {
    margin: 0 0 .5rem; font-size: .8rem; color: #e6c35c; letter-spacing: .06em;
    text-transform: uppercase;
  }
  /* table-layout:fixed with explicit widths on every column except Team keeps
     the table exactly as wide as the card no matter how long a name is --
     without it, the browser auto-sizes the Team column to fit its content
     and the whole table (and the overflow) spills into the next card. */
  .gt-table {
    width: 100%; table-layout: fixed; border-collapse: collapse;
    font-size: .68rem; margin-bottom: .65rem;
  }
  .gt-table th, .gt-table td { padding: .2rem .15rem; text-align: center; overflow: hidden; }
  .gt-table th { color: #7a7a7a; font-weight: 500; border-bottom: 1px solid #242424; }
  .gt-table col.rank, .gt-table th:nth-child(1), .gt-table td:nth-child(1) { width: 1.6em; }
  .gt-table th:nth-child(3), .gt-table td:nth-child(3),
  .gt-table th:nth-child(4), .gt-table td:nth-child(4),
  .gt-table th:nth-child(5), .gt-table td:nth-child(5),
  .gt-table th:nth-child(6), .gt-table td:nth-child(6) { width: 1.6em; }
  .gt-table th:nth-child(7), .gt-table td:nth-child(7),
  .gt-table th:nth-child(8), .gt-table td:nth-child(8) { width: 1.9em; }
  .gt-table th:nth-child(9), .gt-table td:nth-child(9) { width: 2.2em; }
  .gt-table th:nth-child(10), .gt-table td:nth-child(10) { width: 2em; }
  .gt-team { text-align: left !important; }
  .gt-team span {
    display: flex; align-items: center; gap: .3rem; min-width: 0;
  }
  .gt-team span .tn {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .gt-team img { width: 1rem; height: 1rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .gt-pts { font-weight: 700; color: #f2f2f2; }
  /* each match row is clickable -- its full record (score, venue, scorers)
     shows in the Match column, same as clicking a bracket result. */
  .gt-match {
    font-size: .74rem; padding: .3rem .3rem; margin: 0 -.3rem;
    border-top: 1px solid #1e1e1e; cursor: pointer; border-radius: 4px;
  }
  .gt-match:hover { background: #1a1a1a; }
  .gt-match.active { background: #1c1c1c; }
  .gt-mline { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: .35rem; }
  .gt-side { display: flex; align-items: center; gap: .3rem; min-width: 0; }
  .gt-side.b { justify-content: flex-end; text-align: right; }
  .gt-mline img { width: 1.05rem; height: 1.05rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .gt-mline .nm {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .gt-score { color: #e6c35c; font-variant-numeric: tabular-nums; padding: 0 .2rem; white-space: nowrap; }

  .sidebar {
    width: 380px; flex-shrink: 0; border-left: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .sidebar h2, .details h2 {
    margin: 0; padding: .5rem .6rem .38rem; font-size: .82rem; letter-spacing: .12em;
    text-transform: uppercase; color: #9a9a9a; border-bottom: 1px solid #242424;
  }
  /* scrollable, but with the scrollbar chrome hidden on both columns */
  .results, .detail-body {
    scrollbar-width: none;              /* Firefox */
    -ms-overflow-style: none;           /* old Edge */
  }
  .results::-webkit-scrollbar, .detail-body::-webkit-scrollbar { width: 0; height: 0; }
  .results { overflow-y: auto; padding: .25rem .3rem .6rem; flex: 1; }
  .rnd-head {
    font-size: .7rem; letter-spacing: .14em; color: #e6c35c; padding: .5rem .3rem .2rem;
    text-transform: uppercase;
  }
  .row {
    display: grid; grid-template-columns: 1fr auto; gap: .25rem; align-items: center;
    padding: .28rem .3rem; border-radius: 6px; cursor: pointer; margin-bottom: .08rem;
    border: 1px solid transparent;
  }
  .row:hover { background: #171717; }
  .row.future { opacity: .28; }
  .row.latest { background: #1c1c1c; border-color: #e6c35c; }
  .side { display: flex; align-items: center; gap: .3rem; font-size: .82rem; line-height: 1.25; }
  .side + .side { margin-top: .12rem; }
  .side img { width: 1.2rem; height: 1.2rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .side.win { font-weight: 600; color: #ffffff; }
  .side.lose { color: #7c7c7c; }
  .score { font-variant-numeric: tabular-nums; font-size: .76rem; color: #b9b9b9; text-align: right; }
  .date { font-size: .64rem; color: #6a6a6a; margin-top: .12rem; }

  /* right-most column: match info, goals and the embedded highlights video */
  .details {
    width: 460px; flex-shrink: 0; border-left: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .detail-body { overflow-y: auto; padding: 1rem; flex: 1; }
  .d-round {
    font-size: .7rem; letter-spacing: .14em; text-transform: uppercase;
    color: #e6c35c; margin-bottom: .6rem;
  }
  .d-team {
    display: flex; align-items: center; gap: .5rem; font-size: 1rem; padding: .25rem 0;
  }
  .d-team img { width: 1.9rem; height: 1.9rem; border-radius: 50%; object-fit: cover; }
  .d-team .nm { flex: 1; }
  .d-team.win { font-weight: 700; color: #fff; }
  .d-team.win .nm::after { content: " ✓"; color: #e6c35c; }
  .d-team.lose { color: #8a8a8a; }
  .d-team .gl { font-variant-numeric: tabular-nums; font-size: 1.15rem; }
  .d-vs { font-size: .7rem; color: #6a6a6a; padding: .1rem 0 .1rem 2.4rem; }
  .d-meta { font-size: .76rem; color: #8a8a8a; margin: .7rem 0 .2rem; line-height: 1.5; }
  .d-meta b { color: #c8c8c8; font-weight: 600; }

  .sec-h {
    font-size: .64rem; letter-spacing: .12em; text-transform: uppercase;
    color: #7a7a7a; margin: .9rem 0 .3rem;
  }
  .goals { display: flex; flex-direction: column; gap: .3rem; }
  .goal { display: flex; align-items: center; gap: .45rem; font-size: .8rem; color: #d6d6d6; }
  .goal img { width: 1.05rem; height: 1.05rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .goal .min { color: #e6c35c; font-variant-numeric: tabular-nums;
    min-width: 2.6em; text-align: right; }
  .goal .tag { color: #8a8a8a; font-size: .82em; }
  .no-goals { font-size: .74rem; color: #7a7a7a; font-style: italic; }

  .vids { margin-top: .55rem; display: flex; flex-direction: column; gap: .4rem; }
  .vid {
    display: flex; align-items: center; gap: .5rem; text-decoration: none;
    background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    padding: .5rem .6rem; color: #f2f2f2; font-size: .8rem;
  }
  .vid:hover { background: #232323; border-color: #c4302b; }
  .vid .yt {
    flex-shrink: 0; width: 1.6rem; height: 1.1rem; background: #c4302b; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
  }
  .vid .yt::after { content: ""; border-left: .5rem solid #fff;
    border-top: .32rem solid transparent; border-bottom: .32rem solid transparent; margin-left: .12rem; }
  .vid .vt { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
  .vid .vt b { font-size: .8rem; }
  .vid .vt span { font-size: .68rem; color: #9a9a9a;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .no-vid { font-size: .76rem; color: #6a6a6a; margin-top: .8rem; font-style: italic; }
  .src { font-size: .64rem; color: #5a5a5a; margin-top: 1rem; }
  .src a { color: #7a7a7a; }
  .empty { font-size: .82rem; color: #6a6a6a; padding: .5rem 0; line-height: 1.5; }

  .footer { border-top: 1px solid #242424; padding: .5rem 1rem .6rem; }
  .ctrl { display: flex; align-items: center; gap: .7rem; }
  button {
    background: #1d1d1d; color: #f2f2f2; border: 1px solid #3a3a3a; border-radius: 6px;
    padding: .35rem .8rem; cursor: pointer; font-size: .82rem; min-width: 4.6rem;
  }
  button:hover { background: #292929; }
  input[type=range] { flex: 1; accent-color: #e6c35c; height: 1.4rem; cursor: pointer; }
  .count { font-size: .82rem; color: #b9b9b9; min-width: 9rem; font-variant-numeric: tabular-nums; }
  .count b { color: #e6c35c; font-size: .95rem; }

  /* -------------------------------------------------------------------
     Mobile layout (phones/narrow tablets): the desktop's four side-by-side
     columns (years, bracket/groups, results, match) don't fit at any real
     phone width, so below this breakpoint they become stacked, single-view
     "pages" instead -- year picker and match details as full-screen
     overlays, Results promoted to a third tab alongside Bracket/Groups so
     it can share the same full-width stage area rather than needing its
     own column.
     ------------------------------------------------------------------- */
  .yearbar { display: none; }
  @media (max-width: 1024px) {
    body { overflow: hidden; }
    .main { flex-direction: column; min-width: 0; }
    .tab.mobile-only { display: inline-block; }

    .yearbar {
      display: flex; align-items: center; gap: .4rem; padding: .5rem .7rem;
      border-bottom: 1px solid #242424; cursor: pointer; flex-shrink: 0;
    }
    .yearbar b { flex-shrink: 0; color: #e6c35c; }
    .yearbar .yr-flag { flex-shrink: 0; width: 1.1em; height: 1.1em; border-radius: 50%; object-fit: cover; }
    .yearbar .yb-host {
      flex: 1; min-width: 0; color: #b9b9b9;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .yearbar .chev { flex-shrink: 0; margin-left: auto; color: #9a9a9a; }

    /* Fixed in place -- all three always fit on one row, never scroll or wrap. */
    .view-tabs { flex-wrap: nowrap; gap: .15rem; padding: .4rem .2rem 0; justify-content: center; }
    .tab { flex-shrink: 0; padding: .22rem .32rem; font-size: .58rem; }

    .years { display: none; }
    .years.open {
      display: flex; position: fixed; inset: 0; z-index: 50;
      width: 100%; background: #0a0a0a;
    }

    .stage-col, .sidebar, .details { width: 100%; }
    .sidebar { border-left: none; border-top: 1px solid #242424; }
    .sidebar, .details { flex-shrink: 1; }

    /* The Results tab shows .sidebar full-width in place of the bracket/
       groups stage -- but .view-tabs lives inside .stage-col alongside
       .stage, so hiding the whole column would take the tabs down with it,
       leaving no way back to Bracket/Groups. Only .stage itself (and the
       Play/slider footer, meaningless outside the bracket view) hide; the
       column shrinks to just the tabs' own height instead of continuing to
       claim the full remaining space, and .sidebar grows to fill the rest. */
    body.show-results .stage-col { flex: 0 0 auto; }
    body.show-results .stage-col .stage,
    body.show-results .stage-col .footer { display: none; }
    .sidebar { display: none !important; }
    body.show-results .sidebar { display: flex !important; flex: 1; min-height: 0; }

    .details {
      display: none; border-left: none;
    }
    .details.open {
      display: flex; position: fixed; inset: 0; z-index: 60; background: #0a0a0a;
    }

    /* Button+slider on one row; the "N / M games played" count wraps to its
       own row underneath, right-aligned, instead of widening the footer. */
    .ctrl { flex-wrap: wrap; }
    #play { min-width: auto; padding: .3rem .6rem; font-size: .72rem; }
    .count { flex-basis: 100%; min-width: 0; text-align: right; margin-top: .2rem; }

    /* Group cards assume desktop's 460px width; stack them full-width instead.
       The standings table's Team column isn't in the fixed-width nth-child
       list (see the desktop rule above) -- it simply gets whatever width is
       left over after the other (explicitly sized, in em) columns. Desktop's
       460px card leaves it enough room, but a ~360px mobile card doesn't
       unless those columns shrink too, so this isn't just a font-size tweak. */
    .gt-card { width: 100%; }
    .gt-table { font-size: .56rem; }
    .gt-table col.rank, .gt-table th:nth-child(1), .gt-table td:nth-child(1) { width: 1.3em; }
    .gt-table th:nth-child(3), .gt-table td:nth-child(3),
    .gt-table th:nth-child(4), .gt-table td:nth-child(4),
    .gt-table th:nth-child(5), .gt-table td:nth-child(5),
    .gt-table th:nth-child(6), .gt-table td:nth-child(6) { width: 1.3em; }
    .gt-table th:nth-child(7), .gt-table td:nth-child(7),
    .gt-table th:nth-child(8), .gt-table td:nth-child(8) { width: 1.5em; }
    .gt-table th:nth-child(9), .gt-table td:nth-child(9) { width: 1.8em; }
    .gt-table th:nth-child(10), .gt-table td:nth-child(10) { width: 1.6em; }
    .details-close { display: inline-block; float: right; cursor: pointer; font-size: 1.3rem; }
  }
  @media (min-width: 1025px) {
    .details-close { display: none; }
  }

  /* A side-by-side landscape layout (tried in an earlier version) wasn't a
     good experience on a real phone, so landscape is blocked outright
     instead: the app hides and a "please rotate" notice takes its place.
     Gated on (hover: none) and (pointer: coarse) -- touch-only, no mouse --
     specifically so this targets an actual rotated phone and not a desktop
     browser window that just happens to be wider than it is tall; a mouse
     user can size their window however they like. */
  .rotate-notice { display: none; }
  @media (max-width: 1024px) and (orientation: landscape) and (hover: none) and (pointer: coarse) {
    .main { display: none; }
    /* .magnifier lives outside .main (see the HTML), so hiding .main doesn't
       touch it -- and if a touch gets interrupted by the rotation itself
       before touchend fires, its JS state stays "active" with nothing to
       ever clear it. Its z-index is also above .rotate-notice's, so left
       visible it would sit on top of that notice, not behind it. */
    .magnifier { display: none !important; }
    .rotate-notice {
      display: flex; position: fixed; inset: 0; z-index: 100;
      background: #0a0a0a; color: #f2f2f2; flex-direction: column;
      align-items: center; justify-content: center; text-align: center;
      gap: 1rem; padding: 2rem; font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    }
    .rotate-notice .icon { font-size: 3rem; }
    .rotate-notice p { margin: 0; font-size: 1rem; color: #b9b9b9; max-width: 22rem; }
  }
</style>
</head>
<body>
  <div class="main">
    <div class="yearbar" id="yearbar"></div>
    <aside class="years">
      <h2>World Cup</h2>
      <div class="year-list" id="years"></div>
    </aside>
    <div class="stage-col">
      <div class="view-tabs" id="viewTabs"></div>
      <div class="stage" id="stage"></div>
      <div class="footer">
        <div class="ctrl">
          <button id="play">Play</button>
          <input type="range" id="slider" min="0" step="1">
          <div class="count"><b id="n">0</b> <span id="total"></span></div>
        </div>
      </div>
    </div>
    <aside class="sidebar">
      <h2>Results</h2>
      <div class="results" id="results"></div>
    </aside>
    <aside class="details">
      <h2>Match <span class="details-close" id="detailsClose">&times;</span></h2>
      <div class="detail-body" id="details"></div>
    </aside>
  </div>
  <div class="rotate-notice">
    <div class="icon">&#128260;</div>
    <p>Please rotate your device back to portrait to use this page.</p>
  </div>
  <div class="magnifier" id="magnifier"><div class="magnifier-inner" id="magnifierInner"></div></div>

<script id="payload" type="application/json">/*__DATA__*/</script>
<script>
// ALL holds every edition's data plus the shared flag/crest/logo assets; D is
// whichever edition is currently selected (reassigned by selectYear below).
const ALL = JSON.parse(document.getElementById('payload').textContent);
const FLAGS = ALL.flags, CRESTS = ALL.crests, CREST_ASPECT = ALL.crestAspect;
const S = ALL.canvas / (2 * ALL.view);
const C = ALL.colors;
const TAU = Math.PI * 2;
let YEAR = ALL.years[0];
let D = ALL.byYear[YEAR];
let VIEW = 'bracket';   // 'bracket' or 'groups'

const pmod = (x, m) => ((x % m) + m) % m;          // Python-style modulo
const leafAngle = i => (D.startAngleDeg - i * D.angleStepDeg) * Math.PI / 180;
const polar = (r, t) => [r * Math.cos(t), r * Math.sin(t)];
const toPx = (x, y) => [ALL.canvas / 2 + x * S, ALL.canvas / 2 - y * S];
const key = (a, b) => [a, b].sort().join('|');

function arcPoints(r, a, b, n = 40) {
  const d = pmod(b - a + Math.PI, TAU) - Math.PI;
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = a + d * (i / (n - 1));
    pts.push([r * Math.cos(t), r * Math.sin(t)]);
  }
  return pts;
}

// Replays the bracket using only the first `n` results -- mirrors the tree
// build in WC26_Brackets.py (winners advance inward; losers drop out).
function buildTree(n) {
  const winners = new Map();
  for (let i = 0; i < n; i++) {
    const m = D.matches[i];
    winners.set(key(m.a, m.b), m.winner);
  }
  let current = D.teams.map((t, i) => ({ r: D.radii[0], a: leafAngle(i), code: t.code }));
  const gray = [], white = [], dots = [], wdots = [];
  let flags = [], eliminated = new Set(), champion = null, round = 0;

  while (current.length > 1) {
    const next = [], rHere = current[0].r, rNext = D.radii[round + 1];
    for (let i = 0; i < current.length; i += 2) {
      const A = current[i], B = current[i + 1];
      const mid = A.a + (pmod(B.a - A.a + Math.PI, TAU) - Math.PI) / 2;
      let win = null;
      if (A.code && B.code) {
        win = winners.get(key(A.code, B.code)) || null;
        if (win) eliminated.add(win === A.code ? B.code : A.code);
      }
      for (const ch of [A, B]) {
        const seg = [polar(rHere, ch.a), polar(rNext, ch.a)];
        const arc = arcPoints(rNext, ch.a, mid);
        const dest = (win && ch.code === win) ? white : gray;
        dest.push(seg); dest.push(arc);
      }
      if (rNext > 0) { dots.push(polar(rNext, A.a)); dots.push(polar(rNext, B.a)); }
      if (win && rNext > 0) {
        wdots.push(polar(rNext, win === A.code ? A.a : B.a));
        const [mx, my] = polar(rNext, mid);
        let fx = mx, fy = my;
        if (round + 2 <= D.radii.length - 2) {
          [fx, fy] = polar(D.radii[round + 2], mid);
          white.push([[mx, my], [fx, fy]]);
        }
        flags.push({ x: fx, y: fy, code: win, round });
      } else if (win) {
        champion = win;
      }
      next.push({ r: rNext, a: mid, code: win });
    }
    current = next; round++;
  }
  // a flag moves inward rather than repeating: keep only its deepest position
  const deepest = new Map();
  for (const f of flags) {
    const p = deepest.get(f.code);
    if (!p || f.round > p.round) deepest.set(f.code, f);
  }
  const out = [];
  for (const f of deepest.values()) {
    if (f.code === champion) wdots.push([f.x, f.y]); else out.push(f);
  }
  return { gray, white, dots, wdots, flags: out, eliminated, champion };
}

const poly = (pts, color, w) =>
  `<polyline points="${pts.map(p => toPx(p[0], p[1]).map(v => v.toFixed(2)).join(',')).join(' ')}"
    fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`;

function flagUse(x, y, code, d, grey) {
  const [cx, cy] = toPx(x, y), r = d * S / 2;
  const f = grey ? ' filter="url(#grey)"' : '';
  return `<use href="#f-${code}" x="${(cx - r).toFixed(2)}" y="${(cy - r).toFixed(2)}"
    width="${(2 * r).toFixed(2)}" height="${(2 * r).toFixed(2)}" clip-path="url(#circ)"${f}/>
    <circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${r.toFixed(2)}" fill="none"
      stroke="#fff" stroke-width="${Math.max(1.4, r * 0.055).toFixed(2)}"/>`;
}

function nameText(x, y, name, theta) {
  const [cx, cy] = toPx(x, y);
  let rot = pmod(theta * 180 / Math.PI, 360), anchor = 'start';
  if (rot > 90 && rot < 270) { rot += 180; anchor = 'end'; }
  const lines = name.split('\n'), lh = ALL.fs * 1.05;
  const spans = lines.map((ln, i) =>
    `<tspan x="${cx.toFixed(1)}" dy="${(i === 0 ? -(lines.length - 1) / 2 * lh : lh).toFixed(1)}">${ln}</tspan>`).join('');
  return `<text transform="rotate(${(-rot).toFixed(2)} ${cx.toFixed(1)} ${cy.toFixed(1)})"
    x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="${anchor}" dominant-baseline="middle"
    font-size="${ALL.fs}" fill="${C.text}" font-family="Segoe UI, system-ui, sans-serif">${spans}</text>`;
}

// Static half of the drawing: defs + outer ring (flags/crests/names never
// change once a year is selected, so this is only rebuilt on a year switch).
function staticSvg() {
  const symbols = Object.entries(FLAGS).map(([code, uri]) =>
    `<symbol id="f-${code}" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">
       <image x="0" y="0" width="100" height="100" preserveAspectRatio="xMidYMid slice" href="${uri}"/>
     </symbol>`).join('');

  let s = '';
  D.teams.forEach((t, i) => {
    const th = leafAngle(i);
    const [fx, fy] = polar(D.radii[0], th);
    s += flagUse(fx, fy, t.code, ALL.flagD, false);

    const [cx, cy] = toPx(...polar(D.radii[0] + 0.175, th));
    const uri = CRESTS[t.rawName];
    if (uri) {                       // crest: mirrors pad_to_square (0.92, centred)
      const box = ALL.crestD * 0.92 * S, asp = CREST_ASPECT[t.rawName] || 1;
      const w = asp >= 1 ? box : box * asp, h = asp >= 1 ? box / asp : box;
      s += `<image x="${(cx - w / 2).toFixed(2)}" y="${(cy - h / 2).toFixed(2)}"
        width="${w.toFixed(2)}" height="${h.toFixed(2)}" preserveAspectRatio="xMidYMid meet" href="${uri}"/>`;
    } else {                         // no real crest on file: plain acronym badge
      const r = ALL.crestD * 0.92 * S / 2;
      s += `<circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${r.toFixed(2)}"
          fill="#1e1e22" stroke="${C.gold}" stroke-width="${(r * 0.09).toFixed(2)}"/>
        <text x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="middle"
          dominant-baseline="middle" font-size="${(r * 0.55).toFixed(1)}"
          fill="${C.text}" font-family="Segoe UI, system-ui, sans-serif">${t.acronym}</text>`;
    }
    const [tx, ty] = polar(D.radii[0] + 0.30, th);
    s += nameText(tx, ty, t.name, th);
  });

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${ALL.canvas} ${ALL.canvas}">
    <defs>
      ${symbols}
      <clipPath id="circ" clipPathUnits="objectBoundingBox"><circle cx=".5" cy=".5" r=".5"/></clipPath>
      <filter id="grey"><feColorMatrix type="saturate" values="0"/></filter>
    </defs>
    <rect width="${ALL.canvas}" height="${ALL.canvas}" fill="${C.bg}"/>
    <g id="dyn"></g>
    <g>${s}</g>
  </svg>`;
}

// Dynamic half: connectors, advanced flags and the centre, for a given state.
function dynSvg(t) {
  let s = '';
  for (const p of t.gray) s += poly(p, C.line, 1.6);
  for (const [x, y] of t.dots) {
    const [px, py] = toPx(x, y);
    s += `<circle cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" r="3.2" fill="${C.line}"/>`;
  }
  for (const p of t.white) s += poly(p, C.white, 3.0);
  for (const [x, y] of t.wdots) {
    const [px, py] = toPx(x, y);
    s += `<circle cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" r="4" fill="${C.white}"/>`;
  }
  for (const f of t.flags) s += flagUse(f.x, f.y, f.code, ALL.flagD, t.eliminated.has(f.code));

  // The connecting line from the bracket's centre down to the winner's flag
  // is drawn FIRST (SVG paint order = element order), so the logo's
  // background square painted right after covers the part of it that would
  // otherwise cut across the logo.
  if (t.champion) {
    const [c0x, c0y] = toPx(0, 0);
    const [lcx, lcy] = toPx(0, ALL.champFlagY);
    s += `<line x1="${c0x.toFixed(2)}" y1="${c0y.toFixed(2)}" x2="${lcx.toFixed(2)}" y2="${lcy.toFixed(2)}"
      stroke="${C.white}" stroke-width="3.0" stroke-linecap="round"/>`;
  }

  // The tournament logo (or, if somehow missing, a plain year badge) sits
  // above the champion's flag/name/subtitle stack, shifted up from centre.
  // A solid backing square goes behind it -- logo SVGs aren't reliably
  // transparent (some carry their own light/white background), so this
  // keeps every edition looking consistent against the dark page.
  {
    const [bgx, bgy] = toPx(-ALL.logoBgSize / 2, ALL.logoCenterY + ALL.logoBgSize / 2);
    s += `<rect x="${bgx.toFixed(2)}" y="${bgy.toFixed(2)}" width="${(ALL.logoBgSize * S).toFixed(2)}"
      height="${(ALL.logoBgSize * S).toFixed(2)}" fill="${C.bg}"/>`;
  }
  if (D.hasLogo) {
    const [ox, oy] = toPx(-D.logoW / 2, ALL.logoCenterY + D.logoH / 2);
    s += `<image x="${ox.toFixed(2)}" y="${oy.toFixed(2)}" width="${(D.logoW * S).toFixed(2)}"
      height="${(D.logoH * S).toFixed(2)}" preserveAspectRatio="xMidYMid meet" href="${D.logo}"/>`;
  } else {
    const [bx, by] = toPx(0, ALL.logoCenterY), br = 0.22 * S;
    s += `<circle cx="${bx.toFixed(2)}" cy="${by.toFixed(2)}" r="${br.toFixed(2)}"
        fill="#141414" stroke="${C.gold}" stroke-width="3"/>
      <text x="${bx.toFixed(1)}" y="${(by - 0.045 * S).toFixed(1)}" text-anchor="middle"
        dominant-baseline="middle" font-size="30" fill="${C.gold}" font-weight="bold"
        font-family="Segoe UI, system-ui, sans-serif">${D.year}</text>
      <text x="${bx.toFixed(1)}" y="${(by + 0.09 * S).toFixed(1)}" text-anchor="middle"
        dominant-baseline="middle" font-size="9" fill="${C.text}"
        font-family="Segoe UI, system-ui, sans-serif">FIFA WORLD CUP</text>`;
  }

  if (t.champion) {
    const [cx, cy] = toPx(0, ALL.champFlagY), gr = ALL.champD * S / 2 * 1.16;
    s += `<circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${gr.toFixed(2)}"
      fill="${C.gold}" stroke="#8a6d1f" stroke-width="3"/>`;
    s += flagUse(0, ALL.champFlagY, t.champion, ALL.champD, false);
    // one word per line, for multi-word national names
    const words = NAME[t.champion].toUpperCase().split(' ');
    words.forEach((w, i) => {
      const [tx, ty] = toPx(0, D.champTextY + i * ALL.champNameLineDy);
      s += `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="middle"
        dominant-baseline="hanging" font-size="${ALL.champNameFs.toFixed(1)}"
        fill="${C.gold}" font-weight="bold"
        font-family="Segoe UI, system-ui, sans-serif">${w}</text>`;
    });
    const [sx, sy] = toPx(0, D.champTextY + words.length * ALL.champNameLineDy);
    s += `<text x="${sx.toFixed(1)}" y="${sy.toFixed(1)}" text-anchor="middle"
      dominant-baseline="hanging" font-size="${ALL.champSubFs.toFixed(1)}"
      fill="${C.gold}" font-family="Segoe UI, system-ui, sans-serif">${ALL.champSubtitle}</text>`;
  }
  return s;
}

let NAME = {};

function buildResults() {
  let html = '', lastRound = null;
  D.matches.forEach((m, i) => {
    if (m.round !== lastRound) { html += `<div class="rnd-head">${m.round}</div>`; lastRound = m.round; }
    const side = c => `<div class="side ${m.winner === c ? 'win' : 'lose'}">
        <img src="${FLAGS[c]}" alt=""><span>${NAME[c]}</span></div>`;
    html += `<div class="row future" data-i="${i}">
        <div>${side(m.a)}${side(m.b)}<div class="date">${m.date}</div></div>
        <div class="score">${m.score}</div>
      </div>`;
  });
  document.getElementById('results').innerHTML = html;
}
// Delegated on the container, attached once, rather than re-attached to each
// .row every time buildResults() replaces them -- one listener that can't
// end up missing or stale regardless of how many times the year changes.
document.getElementById('results').addEventListener('click', e => {
  const row = e.target.closest('.row');
  if (!row) return;
  setN(+row.dataset.i + 1);
  openDetails();
});

// Mobile only: the Match column is a full-screen overlay there (see the
// .details.open CSS) instead of an always-visible side column, since a
// phone-width screen has no room for a permanent column. Harmless no-op on
// desktop, where .open has no matching rule.
function openDetails() { document.querySelector('.details').classList.add('open'); }
function closeDetails() { document.querySelector('.details').classList.remove('open'); }
document.getElementById('detailsClose').addEventListener('click', closeDetails);

// "Groups" view: standings + results for every group in the current year's
// group stage (replaces the bracket SVG in the stage area; hidden entirely
// for the two editions with no group stage at all, 1934 and 1938).
function hasGroups() { return D.groups && D.groups.length > 0; }

function renderTabs() {
  const el = document.getElementById('viewTabs');
  const tabs = [`<button class="tab${VIEW === 'bracket' ? ' active' : ''}" data-v="bracket">Bracket</button>`];
  if (hasGroups()) {
    tabs.push(`<button class="tab${VIEW === 'groups' ? ' active' : ''}" data-v="groups">Groups</button>`);
  }
  // Mobile only (see .tab.mobile-only CSS): Results has its own permanent
  // column on desktop, so this tab would be redundant there.
  tabs.push(`<button class="tab mobile-only${VIEW === 'results' ? ' active' : ''}" data-v="results">Results</button>`);
  el.innerHTML = tabs.join('');
  el.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => setView(b.dataset.v)));
}

function setView(v) {
  if (v === 'groups' && !hasGroups()) v = 'bracket';
  VIEW = v;
  renderTabs();
  const inGroups = VIEW === 'groups';
  const inResults = VIEW === 'results';
  // Portrait mobile: promotes .sidebar (Results) from "always hidden" to
  // "shown full-width in place of the bracket/groups stage" -- see the
  // body.show-results CSS. No effect on desktop, where .sidebar's own
  // inline style (set just below) already controls its visibility.
  document.body.classList.toggle('show-results', inResults);
  document.querySelector('.footer').style.display = (inGroups || inResults) ? 'none' : '';
  document.querySelector('.sidebar').style.display = inGroups ? 'none' : '';
  if (inGroups) {
    stage.innerHTML = renderGroups();
    document.querySelectorAll('.gt-match').forEach(el =>
      el.addEventListener('click', () => { selectGroupMatch(+el.dataset.gm); openDetails(); }));
    renderDetails(-1);
  } else {
    // Both 'bracket' and 'results' need the real bracket SVG in #stage, even
    // though 'results' hides it via CSS -- a .row click calls setN(), which
    // updates #dyn *inside* that SVG. Coming here straight from 'groups'
    // leaves #stage holding that stale HTML instead, with no #dyn in it at
    // all, so setN() would throw trying to update an element that doesn't
    // exist -- silently aborting before it reaches openDetails().
    stage.innerHTML = staticSvg();
    setN(+slider.value);
    if (inResults) renderDetails(-1);
  }
}

// Every match across every group in the current year, in render order, so a
// click on a .gt-match (which only knows its own flat index) can look its
// full record back up for the Match column.
let groupMatchList = [];

function selectGroupMatch(i) {
  document.querySelectorAll('.gt-match').forEach((el, idx) =>
    el.classList.toggle('active', idx === i));
  renderGroupMatchDetails(groupMatchList[i]);
}

function renderGroups() {
  groupMatchList = [];
  D.groups.forEach(g => groupMatchList.push(...g.matches));

  const matchHtml = m => {
    const gi = groupMatchList.indexOf(m);
    return `<div class="gt-match" data-gm="${gi}">
      <div class="gt-mline">
        <div class="gt-side a"><img src="${FLAGS[m.a]}" alt=""><span class="nm">${NAME[m.a] || m.a}</span></div>
        <span class="gt-score">${m.score}</span>
        <div class="gt-side b"><span class="nm">${NAME[m.b] || m.b}</span><img src="${FLAGS[m.b]}" alt=""></div>
      </div>
    </div>`;
  };

  const cardHtml = g => {
    const rows = g.standings.map((r, i) => `
      <tr>
        <td>${i + 1}</td>
        <td class="gt-team"><span><img src="${FLAGS[r.code]}" alt=""><span class="tn">${NAME[r.code] || r.name}</span></span></td>
        <td>${r.played}</td><td>${r.won}</td><td>${r.drawn}</td><td>${r.lost}</td>
        <td>${r.gf}</td><td>${r.ga}</td><td>${r.gd > 0 ? '+' + r.gd : r.gd}</td>
        <td class="gt-pts">${r.pts}</td>
      </tr>`).join('');
    return `<div class="gt-card">
      <h3>${g.label}</h3>
      <table class="gt-table"><thead><tr>
        <th></th><th style="text-align:left">Team</th><th>P</th><th>W</th><th>D</th><th>L</th>
        <th>GF</th><th>GA</th><th>GD</th><th>Pts</th>
      </tr></thead><tbody>${rows}</tbody></table>
      ${g.matches.map(matchHtml).join('')}
    </div>`;
  };

  if (!hasGroups()) return '<div class="empty">No group-stage data for this edition.</div>';
  return `<div class="groups-view">${D.groups.map(cardHtml).join('')}</div>`;
}

// Right-most column: info + video links for the selected match.
function vidLink(v, label) {
  return `<a class="vid" href="${v.url}" target="_blank" rel="noopener">
      <span class="yt"></span>
      <span class="vt"><b>${label}</b><span>${v.title || v.url}</span></span></a>`;
}

function renderDetails(i) {
  const el = document.getElementById('details');
  if (i < 0 || i >= D.matches.length) {
    el.innerHTML = `<div class="empty">Click a match on the
      left, to see the score, venue and highlights here.</div>`;
    return;
  }
  const m = D.matches[i];
  const sc = /^(\d+)-(\d+)/.exec(m.score);      // leading FT/ET scoreline
  const ga = sc ? sc[1] : '', gb = sc ? sc[2] : '';
  const team = (c, g) => `<div class="d-team ${m.winner === c ? 'win' : 'lose'}">
      <img src="${FLAGS[c]}" alt=""><span class="nm">${NAME[c]}</span>
      <span class="gl">${g}</span></div>`;

  // who scored, and when
  const goalRow = g => `<div class="goal">
      <img src="${FLAGS[g.code]}" alt="">
      <span class="min">${g.min}'</span>
      <span class="nm2">${g.name}${g.og ? ' <span class="tag">(o.g.)</span>'
        : g.pen ? ' <span class="tag">(pen.)</span>' : ''}</span></div>`;
  const goalsBlock = m.goals.length
    ? `<div class="goals">${m.goals.map(goalRow).join('')}</div>`
    : `<div class="no-goals">No goals in normal or extra time.</div>`;

  // link out to the highlights on YouTube (FOX Sports disables embedding)
  const v = m.video || {};
  const links = [];
  if (v.hl) links.push(vidLink(v.hl, 'Highlights'));
  if (v.ext) links.push(vidLink(v.ext, 'Extended highlights'));
  const vidBlock = links.length
    ? `<div class="vids">${links.join('')}</div>`
    : `<div class="no-vid">No highlights video available for this match.</div>`;

  const meta = [`<b>Date:</b> ${m.date}`];
  if (m.ground) meta.push(`<b>Stadium:</b> ${m.ground}`);
  el.innerHTML = `
    <div class="d-round">${m.round}</div>
    ${team(m.a, ga)}
    <div class="d-vs">vs</div>
    ${team(m.b, gb)}
    <div class="d-meta">${meta.join('<br>')}</div>
    <div class="sec-h">Goals</div>
    ${goalsBlock}
    <div class="sec-h">Highlights</div>
    ${vidBlock}
    ${D.sourceChannel ? `<div class="src">Video via
      <a href="${D.sourceChannel}" target="_blank" rel="noopener"
      >${D.sourceChannel.replace('https://www.youtube.com/', '')}</a></div>` : ''}`;
}

// Match column for a clicked group-stage match: same shape as renderDetails,
// minus highlights (group matches have no video links) and accounting for
// draws, which have no winner to bold/checkmark.
function renderGroupMatchDetails(m) {
  const el = document.getElementById('details');
  const sc = /^(\d+)-(\d+)/.exec(m.score);
  const ga = sc ? sc[1] : '', gb = sc ? sc[2] : '';
  const side = c => m.winner ? (m.winner === c ? 'win' : 'lose') : '';
  const team = (c, g) => `<div class="d-team ${side(c)}">
      <img src="${FLAGS[c]}" alt=""><span class="nm">${NAME[c]}</span>
      <span class="gl">${g}</span></div>`;

  const goalRow = g => `<div class="goal">
      <img src="${FLAGS[g.code]}" alt="">
      <span class="min">${g.min}'</span>
      <span class="nm2">${g.name}${g.og ? ' <span class="tag">(o.g.)</span>'
        : g.pen ? ' <span class="tag">(pen.)</span>' : ''}</span></div>`;
  const goalsBlock = m.goals.length
    ? `<div class="goals">${m.goals.map(goalRow).join('')}</div>`
    : `<div class="no-goals">No goals in normal or extra time.</div>`;

  const meta = [`<b>Date:</b> ${m.date}`];
  if (m.ground) meta.push(`<b>Stadium:</b> ${m.ground}`);
  el.innerHTML = `
    <div class="d-round">${m.round}</div>
    ${team(m.a, ga)}
    <div class="d-vs">vs</div>
    ${team(m.b, gb)}
    <div class="d-meta">${meta.join('<br>')}</div>
    <div class="sec-h">Goals</div>
    ${goalsBlock}`;
}

const stage = document.getElementById('stage');
const slider = document.getElementById('slider');
const rows = () => document.querySelectorAll('.row');

// Circular magnifier over the bracket -- touch (phone) and mouse (desktop)
// both drive the same show/move/hide core. Only ever active when #stage
// currently holds the bracket <svg>; in Groups view #stage holds the group
// cards instead, and touches/clicks there need to reach .gt-match normally.
const magnifier = document.getElementById('magnifier');
const magnifierInner = document.getElementById('magnifierInner');
const MAG_SIZE = 255;
const MAG_ZOOM = 2.5;
const MAG_GAP = 30;   // vertical gap between the pointer and the loupe, so it doesn't sit under a finger/cursor
let magnifierActive = false;

function magnifierSvg() { return stage.querySelector('svg'); }

function startMagnifier(x, y) {
  const svg = magnifierSvg();
  if (!svg) return;
  magnifierInner.innerHTML = '';
  magnifierInner.appendChild(svg.cloneNode(true));
  magnifier.classList.add('active');
  magnifierActive = true;
  moveMagnifier(x, y);
}

function moveMagnifier(x, y) {
  if (!magnifierActive) return;
  const svg = magnifierSvg();
  if (!svg) return;
  const rect = svg.getBoundingClientRect();
  const relX = x - rect.left, relY = y - rect.top;

  const centerY = y - MAG_SIZE / 2 - MAG_GAP;
  magnifier.style.left = (x - MAG_SIZE / 2) + 'px';
  magnifier.style.top = (centerY - MAG_SIZE / 2) + 'px';

  magnifierInner.style.width = (rect.width * MAG_ZOOM) + 'px';
  magnifierInner.style.height = (rect.height * MAG_ZOOM) + 'px';
  magnifierInner.style.left = (MAG_SIZE / 2 - relX * MAG_ZOOM) + 'px';
  magnifierInner.style.top = (MAG_SIZE / 2 - relY * MAG_ZOOM) + 'px';
}

function endMagnifier() { magnifier.classList.remove('active'); magnifierActive = false; }

// Touch browsers fire a synthetic mouseenter/mousemove shortly after a real
// touch ends (for sites that only listen for mouse events) -- with no
// matching mouseleave, since the "mouse" never actually moved away. Left
// unguarded, that would pop the magnifier back up right after touchend just
// hid it. Ignoring mouse events for a moment after any real touch avoids that.
let lastTouchAt = 0;
const isRecentTouch = () => Date.now() - lastTouchAt < 800;
// The magnifier is a portrait-only affordance -- phones in landscape are
// already blocked outright (see .rotate-notice), but this also covers a
// touch-capable tablet wide enough to dodge that width check, and a mouse
// on a desktop window that's simply wider than it is tall (which is most
// desktop windows), by the same rule.
const isLandscape = () => window.matchMedia('(orientation: landscape)').matches;

stage.addEventListener('touchstart', e => {
  lastTouchAt = Date.now();
  if (!magnifierSvg() || isLandscape()) return;   // Groups view: let the touch synthesize a normal click on .gt-match
  const t = e.touches[0];
  startMagnifier(t.clientX, t.clientY);
  e.preventDefault();   // stop a held touch from starting the browser's own long-press/text-select gesture
}, { passive: false });
stage.addEventListener('touchmove', e => {
  lastTouchAt = Date.now();
  if (!magnifierActive) return;
  const t = e.touches[0];
  moveMagnifier(t.clientX, t.clientY);
  e.preventDefault();   // nothing inside .stage scrolls, so this just stops the page fighting the drag
}, { passive: false });
stage.addEventListener('touchend', e => { lastTouchAt = Date.now(); endMagnifier(); });
stage.addEventListener('touchcancel', e => { lastTouchAt = Date.now(); endMagnifier(); });

stage.addEventListener('mouseenter', e => {
  if (!isRecentTouch() && !isLandscape() && magnifierSvg()) startMagnifier(e.clientX, e.clientY);
});
stage.addEventListener('mousemove', e => { if (!isRecentTouch() && !isLandscape()) moveMagnifier(e.clientX, e.clientY); });
stage.addEventListener('mouseleave', e => { if (!isRecentTouch()) endMagnifier(); });

// A screen rotation can interrupt an in-progress touch before touchend ever
// fires, leaving the magnifier's JS state stuck "active" (the CSS above
// force-hides it regardless, but this keeps the two in sync).
window.addEventListener('orientationchange', endMagnifier);

// Clicking a match sets the slider to it (setN), so the bracket advances to
// that game and its details are shown -- the current game is one and the same.
function setN(n) {
  n = Math.max(0, Math.min(D.matches.length, n));
  slider.value = n;
  document.getElementById('n').textContent = n;
  document.getElementById('dyn').innerHTML = dynSvg(buildTree(n));
  rows().forEach((r, i) => {
    r.classList.toggle('future', i >= n);
    r.classList.toggle('latest', i === n - 1);
  });
  const cur = document.querySelector('.row.latest');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
  renderDetails(n - 1);   // details follow the most recently played match
}

function hostFlagsAndNames(y) {
  const host = ALL.byYear[y].host || [];
  const flags = host.map(h => h.code && FLAGS[h.code]
    ? `<img class="yr-flag" src="${FLAGS[h.code]}">` : '').join('');
  return { flags, names: host.map(h => h.name).join(' & ') };
}

function buildYearList() {
  const html = ALL.years.map(y => {
    const { flags, names } = hostFlagsAndNames(y);
    return `<div class="year-row${y === YEAR ? ' active' : ''}" data-y="${y}">` +
      `<div class="yr-top"><span class="yr-num">${y} –</span>${flags}</div>` +
      `<div class="yr-host">${names}</div>` +
      `</div>`;
  }).join('');
  document.getElementById('years').innerHTML = html;
  document.querySelectorAll('.year-row').forEach(r =>
    r.addEventListener('click', () => { selectYear(+r.dataset.y); closeYearMenu(); }));
}

// Mobile only: the year list is a full-screen overlay there (see .years.open
// CSS), toggled by tapping the compact bar above the stage that shows the
// currently-selected year. Harmless no-op on desktop, where .years is always
// visible and #yearbar itself is hidden by CSS.
function renderYearBar() {
  const { flags, names } = hostFlagsAndNames(YEAR);
  document.getElementById('yearbar').innerHTML =
    `<b>${YEAR}</b>${flags}<span class="yb-host">${names}</span><span class="chev">&#9662;</span>`;
}
function toggleYearMenu() { document.querySelector('.years').classList.toggle('open'); }
function closeYearMenu() { document.querySelector('.years').classList.remove('open'); }
document.getElementById('yearbar').addEventListener('click', toggleYearMenu);

// Switches the whole page to a different edition: new bracket geometry, team
// roster, results list and match details. Shared assets (FLAGS/CRESTS/logo)
// don't change, so only the per-year pieces are rebuilt.
function selectYear(y) {
  if (timer) { clearInterval(timer); timer = null; document.getElementById('play').textContent = 'Play'; }
  YEAR = y;
  D = ALL.byYear[YEAR];
  VIEW = 'bracket';
  NAME = {};
  D.teams.forEach(t => NAME[t.code] = t.name.replace(/\n/g, ' '));
  // group standings cover every entrant, including teams knocked out before
  // the bracket's entry round, so they're not all in D.teams above
  (D.groups || []).forEach(g => g.standings.forEach(s => { if (!NAME[s.code]) NAME[s.code] = s.name; }));
  document.querySelectorAll('.year-row').forEach(r => r.classList.toggle('active', +r.dataset.y === YEAR));
  document.body.classList.remove('show-results');
  document.querySelector('.footer').style.display = '';
  document.querySelector('.sidebar').style.display = '';
  renderYearBar();
  renderTabs();
  stage.innerHTML = staticSvg();
  buildResults();
  slider.max = D.matches.length;
  document.getElementById('total').textContent = '/ ' + D.matches.length + ' games played';
  setN(D.matches.length);
}

let timer = null;

slider.addEventListener('input', () => setN(+slider.value));
document.getElementById('play').addEventListener('click', e => {
  if (timer) { clearInterval(timer); timer = null; e.target.textContent = 'Play'; return; }
  if (+slider.value >= D.matches.length) setN(0);
  e.target.textContent = 'Pause';
  timer = setInterval(() => {
    if (+slider.value >= D.matches.length) {
      clearInterval(timer); timer = null;
      document.getElementById('play').textContent = 'Play';
    } else setN(+slider.value + 1);
  }, 600);
});

buildYearList();
selectYear(YEAR);
// selectYear -> setN scrolls the latest result row into view, which can drag
// the whole (horizontally-overflowing, see .main's min-width) page sideways
// on a narrow viewport; pin it back to the leftmost column on load. (body's
// overflow-x propagates to the viewport per the CSS Overflow spec, since
// :root/html has no overflow of its own, so this -- not body.scrollLeft --
// is what actually controls that scroll position.) Deferred a frame: the
// scrollIntoView above doesn't take effect until the next layout, so an
// immediate reset here would just be overridden by it right afterward.
requestAnimationFrame(() => window.scrollTo(0, 0));
</script>
</body>
</html>
"""


def main(out_path=None):
    out_path = out_path or OUT_PATH
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    payload = build_payload()
    html = HTML.replace("/*__DATA__*/", json.dumps(payload))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Saved interactive bracket to {out_path} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
