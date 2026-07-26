"""Build highlights_data/2026_worldcup_highlights.json from FOX Sports' public
YouTube catalog. 2026-specific: FOX Sports held the US broadcast rights for
that edition only, so this script (unlike the rest of the project) doesn't
generalize across editions."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from yt_dlp import YoutubeDL


ROOT = Path(__file__).resolve().parent
SOURCE_PATH = ROOT / "worldcup_data" / "2026.json"
OUTPUT_PATH = ROOT / "highlights_data" / "2026_worldcup_highlights.json"
CHANNEL_URL = "https://www.youtube.com/@foxsports/videos"
ALIASES = {
    "bosnia & herzegovina": "bosnia and herzegovina",
    "czech republic": "czechia",
    "turkey": "turkiye",
    "usa": "united states",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def team_name(value: str) -> str:
    return ALIASES.get(value.lower(), value)


def main() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    options = {
        "extract_flat": True,
        "quiet": True,
        "playlistend": 1000,
        "skip_download": True,
    }
    with YoutubeDL(options) as ydl:
        catalog = ydl.extract_info(CHANNEL_URL, download=False)["entries"]

    videos = []
    for item in catalog:
        title = item.get("title") or ""
        normalized = normalize(title)
        if "2026 fifa world cup" not in normalized or "highlights" not in normalized:
            continue
        videos.append(
            {
                "title": title,
                "normalized": normalized,
                "url": f"https://www.youtube.com/watch?v={item['id']}",
            }
        )

    output = {
        "competition": source.get("name"),
        "source_channel": "https://www.youtube.com/@foxsports",
        "generated_from": "worldcup_data/2026.json",
        "matches": [],
    }
    for match in source["matches"]:
        first = normalize(team_name(match["team1"]))
        second = normalize(team_name(match["team2"]))
        candidates = [
            video
            for video in videos
            if first in video["normalized"] and second in video["normalized"]
        ]
        standard = next(
            (video for video in candidates if "extended highlights" not in video["normalized"]),
            None,
        )
        extended = next(
            (video for video in candidates if "extended highlights" in video["normalized"]),
            None,
        )
        record = {
            "match_number": match.get("num"),
            "round": match["round"],
            "date": match["date"],
            "team1": match["team1"],
            "team2": match["team2"],
            "highlights": ({"title": standard["title"], "url": standard["url"]} if standard else None),
            "extended_highlights": ({"title": extended["title"], "url": extended["url"]} if extended else None),
        }
        output["matches"].append(record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
