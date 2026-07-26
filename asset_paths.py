"""Filename conventions and SVG inspection for the bracket's visual assets.

Given a team name or tournament year, these say what file that asset lives
in (or would live in, once downloaded) and what shape it is -- nothing here
reads or writes over the network. Shared by download_resources.py (to know
where to save a file), WC_Brackets.py and build_web.py (to know where to
load one from).
"""

from __future__ import annotations

import re


def crest_filename(team_name: str) -> str:
    """Return the country-name crest filename used by the bracket."""
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", team_name.replace("&", "and"))
    return f"{safe_name.strip('_')}_crest.svg"


def tournament_logo_filename(year: int) -> str:
    """Return the filename used for a World Cup edition's tournament logo."""
    return f"{year}_worldcup_logo.svg"


def svg_aspect(path: str) -> float:
    """Intrinsic width/height of an SVG (viewBox first, else width/height --
    some crests, e.g. Portugal, declare width/height and no viewBox). Shared
    by WC_Brackets.py and build_web.py so tournament logos and crests of very
    different native shapes (a tall 1930-era emblem vs. a wide one) can be
    fit into a consistent bounding box instead of assuming one aspect ratio."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        head = fh.read(1200)
    m = re.search(r'viewBox\s*=\s*"[\d.eE+\-]+\s+[\d.eE+\-]+\s+'
                  r'([\d.eE+\-]+)\s+([\d.eE+\-]+)"', head)
    if not m:
        mw = re.search(r'\bwidth\s*=\s*"([\d.]+)', head)
        mh = re.search(r'\bheight\s*=\s*"([\d.]+)', head)
        if mw and mh:
            return float(mw.group(1)) / float(mh.group(1))
        return 1.0
    w, h = float(m.group(1)), float(m.group(2))
    return w / h if h else 1.0
