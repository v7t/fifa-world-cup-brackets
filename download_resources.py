"""Download the raw data and visual assets used by the World Cup bracket.

Pipeline, in order:
  1. worldcup_data/<year>.json        -- match results, from openfootball/worldcup.json
  2. tournament_logos/<year>_worldcup_logo.svg -- from football-logos.cc/tournaments/
  3. historical_teams.list_all_countries() -- every country that has ever
                                          played a World Cup match, as
                                          {ISO 3166-1 alpha-2 code: name}
  4. country_flags/<code>.svg         -- from hatscripts/circle-flags
  5. country_crests/<name>_crest.svg  -- from football-logos.cc/national-teams/

Every download step skips a file that already exists (pass overwrite=True, or
--overwrite on the CLI, to force a redownload).

Match-data loading and knockout-bracket reconstruction live in
bracket_data.py; crest/logo filename conventions live in asset_paths.py --
neither of those does any downloading, so they aren't duplicated here.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import historical_teams
from asset_paths import crest_filename, tournament_logo_filename
from bracket_data import (
    ALL_YEARS,
    DATA_DIR,
    extract_knockout_results,
    print_entry_stage_teams,
)


SCRIPT_DIR = Path(__file__).resolve().parent
LOGO_DIR = SCRIPT_DIR / "tournament_logos"
FLAG_DIR = SCRIPT_DIR / "country_flags"
CREST_DIR = SCRIPT_DIR / "country_crests"

WORLDCUP_JSON_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/"
    "master/{year}/worldcup.json"
)
TOURNAMENT_LOGO_URL_TEMPLATE = "https://football-logos.cc/tournaments/fifa-world-cup-{year}/"
NATIONAL_TEAMS_URL = "https://football-logos.cc/national-teams/"
CIRCLE_FLAG_URL_TEMPLATE = "https://hatscripts.github.io/circle-flags/flags/{code}.svg"
USER_AGENT = "WorldCupBrackets-resource-downloader/1.0"

# football-logos.cc names a handful of federations differently from the
# openfootball data; normalize before matching a team name to its crest.
_CREST_NAME_ALIASES = {
    "ivory coast": "cote d ivoire",
    "cape verde": "cabo verde",
    "dr congo": "congo dr",
    "ireland": "republic of ireland",
    "united arab emirates": "uae",
}


class _CrestIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.crests: dict[str, tuple[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return

        attributes = dict(attrs)
        if "data-logo-downloads" not in attributes:
            return

        category = attributes.get("data-category-id")
        logo_id = attributes.get("data-logo-id")
        svg_hash = attributes.get("data-svg-hash")
        if category and logo_id and svg_hash:
            self.crests[category] = (logo_id, svg_hash)


def _name_tokens(name: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = normalized.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    alias = _CREST_NAME_ALIASES.get(normalized, normalized)
    return tuple(alias.split())


def _crest_category(team_name: str, categories: set[str]) -> str | None:
    wanted_tokens = _name_tokens(team_name)
    for category in categories:
        category_tokens = _name_tokens(category)
        if category_tokens == wanted_tokens or sorted(category_tokens) == sorted(wanted_tokens):
            return category
    return None


def _download(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not download {url}: {error}") from error


def _write_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)
    return path


# ----------------------------------------------------------------------
# 1. Match results: worldcup_data/<year>.json
# ----------------------------------------------------------------------

def download_worldcup_json(
    year: int,
    destination: str | Path | None = None,
    *,
    timeout: float = 30.0,
) -> Path:
    """Download and validate the OpenFootball World Cup JSON file for ``year``."""
    destination = Path(destination) if destination is not None else DATA_DIR / f"{year}.json"
    content = _download(WORLDCUP_JSON_URL_TEMPLATE.format(year=year), timeout)

    try:
        json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("The downloaded World Cup file is not valid JSON") from error

    return _write_bytes(destination, content)


def download_all_worldcup_data(
    years: list[int] | None = None,
    *,
    overwrite: bool = False,
    timeout: float = 30.0,
) -> dict[int, Path]:
    """Download every edition's match results into worldcup_data/<year>.json,
    skipping editions already on disk unless ``overwrite`` is true."""
    years = ALL_YEARS if years is None else years
    output_paths: dict[int, Path] = {}
    for year in years:
        dest = DATA_DIR / f"{year}.json"
        if dest.exists() and not overwrite:
            output_paths[year] = dest
            continue
        output_paths[year] = download_worldcup_json(year, dest, timeout=timeout)
    return output_paths


# ----------------------------------------------------------------------
# 2. Tournament logos: tournament_logos/<year>_worldcup_logo.svg
# ----------------------------------------------------------------------

def _make_download_browser(destination: Path):
    """A headless-ish, off-screen Chrome configured to auto-save downloads
    (no save-as dialog) into ``destination``. Shared by every football-logos.cc
    scraper below, since they all drive the same site's SVG download button."""
    from selenium import webdriver

    options = webdriver.ChromeOptions()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-position=-32000,-32000")
    options.add_argument("--window-size=800,600")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(destination.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )
    return webdriver.Chrome(options=options)


def _download_svg_via_button(browser, url: str, destination: Path, *, timeout: float) -> Path:
    """Navigate to ``url`` and click football-logos.cc's SVG download button,
    returning the path of the newly-downloaded file. Retries a few times
    since the button occasionally doesn't fire a download on the first click."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as conditions
    from selenium.webdriver.support.ui import WebDriverWait

    attempt_timeout = max(5.0, timeout / 3)
    for _ in range(3):
        browser.get(url)
        button = WebDriverWait(browser, timeout).until(
            conditions.element_to_be_clickable((By.CSS_SELECTOR, "[data-logo-svg-download-button]"))
        )
        files_before = set(destination.iterdir())
        button.click()

        deadline = time.monotonic() + attempt_timeout
        while time.monotonic() < deadline:
            new_svg_files = [
                path for path in set(destination.iterdir()) - files_before
                if path.suffix.lower() == ".svg"
            ]
            if new_svg_files:
                return max(new_svg_files, key=lambda path: path.stat().st_mtime)
            time.sleep(0.2)
    raise RuntimeError(f"Timed out downloading the SVG from {url}")


def download_tournament_logos(
    years: list[int] | None = None,
    destination: str | Path = LOGO_DIR,
    *,
    overwrite: bool = False,
    timeout: float = 30.0,
) -> dict[int, Path]:
    """Download FIFA World Cup tournament-logo SVGs from football-logos.cc,
    one per edition (default: every edition in ALL_YEARS).

    Files are named ``<year>_worldcup_logo.svg``. Existing files are
    preserved unless ``overwrite`` is true.
    """
    destination = Path(destination)
    years = ALL_YEARS if years is None else years
    output_paths = {year: destination / tournament_logo_filename(year) for year in years}
    pending = [year for year in years if overwrite or not output_paths[year].exists()]
    if not pending:
        return output_paths

    destination.mkdir(parents=True, exist_ok=True)
    browser = _make_download_browser(destination)
    try:
        for year in pending:
            downloaded_path = _download_svg_via_button(
                browser, TOURNAMENT_LOGO_URL_TEMPLATE.format(year=year),
                destination, timeout=timeout,
            )
            content = downloaded_path.read_bytes()
            if b"<svg" not in content[:1000].lower():
                raise RuntimeError(f"The downloaded logo for {year} is not an SVG")
            downloaded_path.replace(output_paths[year])
    finally:
        browser.quit()

    return output_paths


# ----------------------------------------------------------------------
# 4. Circular flags: country_flags/<code>.svg
# ----------------------------------------------------------------------

def download_country_flags(
    countries: dict[str, str] | None = None,
    destination: str | Path = FLAG_DIR,
    *,
    overwrite: bool = False,
    timeout: float = 30.0,
) -> dict[str, Path]:
    """Download a circular flag SVG per country code from hatscripts/circle-flags.

    ``countries`` is a {code: name} mapping (default:
    historical_teams.list_all_countries()). Existing files are preserved
    unless ``overwrite`` is true. A few defunct-entity codes (su, yu, zr, cs,
    dd, and Dutch East Indies' id) predate that repo's coverage and will fail
    to download; those already have a manually sourced historical flag
    checked into country_flags/, so a failure there just leaves that file in
    place instead of overwriting it with nothing.
    """
    destination = Path(destination)
    countries = historical_teams.list_all_countries() if countries is None else countries
    output_paths = {code: destination / f"{code}.svg" for code in countries}
    pending = [code for code in countries if overwrite or not output_paths[code].exists()]
    if not pending:
        return output_paths

    destination.mkdir(parents=True, exist_ok=True)
    failed = []
    for code in pending:
        try:
            content = _download(CIRCLE_FLAG_URL_TEMPLATE.format(code=code), timeout)
            if b"<svg" not in content[:200].lower():
                raise RuntimeError("downloaded file is not an SVG")
            _write_bytes(output_paths[code], content)
        except Exception as exc:
            failed.append(code)
            print(f"[warn] flag download failed for {code} ({countries[code]}): {exc}")
    if failed:
        print(f"[info] flag download failed for: {', '.join(sorted(failed))}")

    return output_paths


# ----------------------------------------------------------------------
# 5. Federation crests: country_crests/<name>_crest.svg
# ----------------------------------------------------------------------

def download_country_crests(
    countries: dict[str, str] | None = None,
    destination: str | Path = CREST_DIR,
    *,
    overwrite: bool = False,
    timeout: float = 30.0,
) -> dict[str, Path]:
    """Download a federation crest SVG per country from football-logos.cc.

    ``countries`` is a {code: name} mapping (default:
    historical_teams.list_all_countries()). Files are named
    ``<name>_crest.svg``. Existing files are preserved unless ``overwrite``
    is true. Defunct entities (Soviet Union, Yugoslavia, Czechoslovakia,
    Zaire, East Germany, Dutch East Indies, Serbia and Montenegro) aren't
    listed on football-logos.cc -- it only carries current national
    federations -- and are silently skipped; they already fall back to a
    generated shield badge in the bracket.
    """
    destination = Path(destination)
    countries = historical_teams.list_all_countries() if countries is None else countries
    output_paths = {
        code: destination / crest_filename(name) for code, name in countries.items()
    }
    pending = {
        code: name for code, name in countries.items()
        if overwrite or not output_paths[code].exists()
    }
    if not pending:
        return output_paths

    index_html = _download(NATIONAL_TEAMS_URL, timeout).decode("utf-8")
    parser = _CrestIndexParser()
    parser.feed(index_html)

    categories = {
        code: _crest_category(name, set(parser.crests)) for code, name in pending.items()
    }
    resolved = {code: cat for code, cat in categories.items() if cat is not None}
    skipped = sorted(countries[code] for code, cat in categories.items() if cat is None)
    if skipped:
        print(f"[info] no crest available on football-logos.cc for: {', '.join(skipped)}")
    if not resolved:
        return output_paths

    destination.mkdir(parents=True, exist_ok=True)
    browser = _make_download_browser(destination)
    failed = []
    try:
        for code, category in resolved.items():
            logo_id, _ = parser.crests[category]
            try:
                downloaded_path = _download_svg_via_button(
                    browser, f"https://football-logos.cc/{category}/{logo_id}/",
                    destination, timeout=timeout,
                )
                content = downloaded_path.read_bytes()
                if b"<svg" not in content[:1000].lower():
                    raise RuntimeError("downloaded file is not an SVG")
                downloaded_path.replace(output_paths[code])
            except Exception as exc:
                # one bad page (e.g. a persistent timeout) shouldn't cost the
                # whole batch -- log it and keep going with the rest
                failed.append(countries[code])
                print(f"[warn] crest download failed for {countries[code]}: {exc}")
    finally:
        browser.quit()
    if failed:
        print(f"[info] crest download failed for: {', '.join(sorted(failed))}")

    return output_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--year", type=int, default=2026,
        help="edition to refresh when no pipeline step is given (default: 2026)",
    )
    parser.add_argument("--json", action="store_true",
                         help="step 1: download worldcup_data/<year>.json for every edition")
    parser.add_argument("--logos", action="store_true",
                         help="step 2: download tournament_logos/<year>_worldcup_logo.svg")
    parser.add_argument("--list-countries", action="store_true",
                         help="step 3: print every country that has ever played a World Cup "
                              "match, with its flag code")
    parser.add_argument("--flags", action="store_true",
                         help="step 4: download country_flags/<code>.svg")
    parser.add_argument("--crests", action="store_true",
                         help="step 5: download country_crests/<name>_crest.svg")
    parser.add_argument("--all", action="store_true",
                         help="run steps 1-2 and 4-5 in order (implies --list-countries' data "
                              "collection, without printing it)")
    parser.add_argument("--overwrite", action="store_true",
                         help="redownload files that already exist (default: skip them)")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    steps_requested = any([
        args.json, args.logos, args.list_countries, args.flags, args.crests, args.all,
    ])
    if not steps_requested:
        # Quick day-to-day refresh: just the one edition's results + bracket
        # status, without touching logos/flags/crests.
        json_path = download_worldcup_json(args.year, DATA_DIR / f"{args.year}.json",
                                            timeout=args.timeout)
        print(f"Downloaded {json_path}")
        print_entry_stage_teams(json_path, args.year)
        completed = sum(
            result["winner"] is not None
            for result in extract_knockout_results(json_path, args.year)
        )
        print(f"Extracted {completed} completed knockout result(s)")
        return

    if args.all or args.json:
        paths = download_all_worldcup_data(overwrite=args.overwrite, timeout=args.timeout)
        print(f"worldcup_data: {len(paths)} edition(s) in {DATA_DIR}")

    if args.all or args.logos:
        paths = download_tournament_logos(overwrite=args.overwrite, timeout=args.timeout)
        print(f"tournament_logos: {len(paths)} logo(s) in {LOGO_DIR}")

    countries = None
    if args.all or args.list_countries or args.flags or args.crests:
        countries = historical_teams.list_all_countries()
        if args.list_countries:
            for code, name in sorted(countries.items(), key=lambda kv: kv[1]):
                print(f"{code:6} {name}")
            print(f"{len(countries)} countries total")

    if args.all or args.flags:
        paths = download_country_flags(countries, overwrite=args.overwrite, timeout=args.timeout)
        print(f"country_flags: {len(paths)} flag(s) in {FLAG_DIR}")

    if args.all or args.crests:
        paths = download_country_crests(countries, overwrite=args.overwrite, timeout=args.timeout)
        print(f"country_crests: {len(paths)} crest(s) in {CREST_DIR}")


if __name__ == "__main__":
    main()
