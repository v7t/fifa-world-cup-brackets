"""Static team metadata spanning every FIFA World Cup team name that has
appeared in worldcup_data/*.json (1930-2026), independent of any one year.

FLAG_CODE maps a team name (as it appears in the OpenFootball data) to a
country_flags/flags_png code. Defunct nations use a minted code with a real
historical flag sourced from Wikimedia Commons (su, yu, zr, cs); a couple of
defunct entities intentionally reuse a still-existing flag design because
that actually was their flag (Czechoslovakia -> cz, West Germany -> de).
Dutch East Indies (1938) is the colonial-era precursor of Indonesia, so it
uses Indonesia's flag code (id), not the Netherlands'.

ACRONYM is only used as a label on the generated shield badge for teams
without a real crest SVG in country_crests/ (see WC_Brackets.load_logo_image) --
it does not need to be the exact historical federation acronym.
"""

from bracket_data import ALL_YEARS, load_worldcup_json, year_source

FLAG_CODE = {
    "Algeria": "dz", "Angola": "ao", "Argentina": "ar", "Australia": "au",
    "Austria": "at", "Belgium": "be", "Bolivia": "bo",
    "Bosnia & Herzegovina": "ba", "Bosnia-Herzegovina": "ba", "Brazil": "br",
    "Bulgaria": "bg", "Cameroon": "cm", "Canada": "ca", "Cape Verde": "cv",
    "Chile": "cl", "China": "cn", "Colombia": "co", "Costa Rica": "cr",
    "Croatia": "hr", "Cuba": "cu", "Curaçao": "cw", "Czech Republic": "cz",
    "Czechoslovakia": "cz", "Côte d'Ivoire": "ci", "Ivory Coast": "ci",
    "DR Congo": "cd", "Denmark": "dk", "Dutch East Indies": "id",
    "East Germany": "dd", "Ecuador": "ec", "Egypt": "eg",
    "El Salvador": "sv", "England": "gb-eng", "France": "fr",
    "Germany": "de", "West Germany": "de", "Ghana": "gh", "Greece": "gr",
    "Haiti": "ht", "Honduras": "hn", "Hungary": "hu", "Iceland": "is",
    "Iran": "ir", "Iraq": "iq", "Ireland": "ie", "Israel": "il",
    "Italy": "it", "Jamaica": "jm", "Japan": "jp", "Jordan": "jo",
    "Kuwait": "kw", "Mexico": "mx", "Morocco": "ma", "Netherlands": "nl",
    "New Zealand": "nz", "Nigeria": "ng", "North Korea": "kp",
    "Northern Ireland": "gb-nir", "Norway": "no", "Panama": "pa",
    "Paraguay": "py", "Peru": "pe", "Poland": "pl", "Portugal": "pt",
    "Qatar": "qa", "Romania": "ro", "Russia": "ru", "Saudi Arabia": "sa",
    "Scotland": "gb-sct", "Senegal": "sn", "Serbia": "rs",
    "Serbia and Montenegro": "cs", "Slovakia": "sk", "Slovenia": "si",
    "South Africa": "za", "South Korea": "kr", "Soviet Union": "su",
    "Spain": "es", "Sweden": "se", "Switzerland": "ch", "Togo": "tg",
    "Trinidad and Tobago": "tt", "Tunisia": "tn", "Turkey": "tr",
    "USA": "us", "United States": "us", "Ukraine": "ua",
    "United Arab Emirates": "ae", "Uruguay": "uy", "Uzbekistan": "uz",
    "Wales": "gb-wls", "Yugoslavia": "yu", "Zaire": "zr",
    "Democratic Republic of Congo": "cd", "Cabo Verde": "cv",
    "Bosnia and Herzegovina": "ba", "Cote d'Ivoire": "ci",
}

ACRONYM = {
    "Algeria": "ALG", "Angola": "ANG", "Argentina": "ARG", "Australia": "AUS",
    "Austria": "AUT", "Belgium": "BEL", "Bolivia": "BOL",
    "Bosnia & Herzegovina": "BIH", "Bosnia-Herzegovina": "BIH",
    "Brazil": "BRA", "Bulgaria": "BUL", "Cameroon": "CMR", "Canada": "CAN",
    "Cape Verde": "CPV", "Chile": "CHI", "China": "CHN", "Colombia": "COL",
    "Costa Rica": "CRC", "Croatia": "CRO", "Cuba": "CUB", "Curaçao": "CUW",
    "Czech Republic": "CZE", "Czechoslovakia": "TCH",
    "Côte d'Ivoire": "CIV", "Ivory Coast": "CIV", "DR Congo": "COD",
    "Denmark": "DEN", "Dutch East Indies": "DEI", "East Germany": "GDR",
    "Ecuador": "ECU", "Egypt": "EGY", "El Salvador": "SLV",
    "England": "ENG", "France": "FRA", "Germany": "GER",
    "West Germany": "FRG", "Ghana": "GHA", "Greece": "GRE", "Haiti": "HAI",
    "Honduras": "HON", "Hungary": "HUN", "Iceland": "ISL", "Iran": "IRN",
    "Iraq": "IRQ", "Ireland": "IRL", "Israel": "ISR", "Italy": "ITA",
    "Jamaica": "JAM", "Japan": "JPN", "Jordan": "JOR", "Kuwait": "KUW",
    "Mexico": "MEX", "Morocco": "MAR", "Netherlands": "NED",
    "New Zealand": "NZL", "Nigeria": "NGA", "North Korea": "PRK",
    "Northern Ireland": "NIR", "Norway": "NOR", "Panama": "PAN",
    "Paraguay": "PAR", "Peru": "PER", "Poland": "POL", "Portugal": "POR",
    "Qatar": "QAT", "Romania": "ROU", "Russia": "RUS",
    "Saudi Arabia": "KSA", "Scotland": "SCO", "Senegal": "SEN",
    "Serbia": "SRB", "Serbia and Montenegro": "SCG", "Slovakia": "SVK",
    "Slovenia": "SVN", "South Africa": "RSA", "South Korea": "KOR",
    "Soviet Union": "URS", "Spain": "ESP", "Sweden": "SWE",
    "Switzerland": "SUI", "Togo": "TOG", "Trinidad and Tobago": "TRI",
    "Tunisia": "TUN", "Turkey": "TUR", "USA": "USA", "United States": "USA",
    "Ukraine": "UKR", "United Arab Emirates": "UAE", "Uruguay": "URU",
    "Uzbekistan": "UZB", "Wales": "WAL", "Yugoslavia": "YUG",
    "Zaire": "ZAI", "Democratic Republic of Congo": "COD",
    "Cabo Verde": "CPV", "Bosnia and Herzegovina": "BIH",
    "Cote d'Ivoire": "CIV",
}


def flag_code(team_name: str) -> str | None:
    return FLAG_CODE.get(team_name)


def acronym(team_name: str) -> str:
    return ACRONYM.get(team_name, team_name[:3].upper())


# Explicit line-break overrides for the outer-ring label, checked before the
# generic word-wrap in display_name(). Mainly for names where the data's own
# short form (e.g. "USA") should instead show the full country name, split
# where it reads best rather than wherever the generic wrap width lands.
DISPLAY_NAME_OVERRIDE = {
    "USA": "United States\nof America",
    "United States": "United States\nof America",
}


def display_name(team_name: str, wrap_width: int = 13) -> str:
    """Outer-ring display label for a team name: an explicit override if one
    exists, else the generic word-wrap for names too long to fit one line."""
    if team_name in DISPLAY_NAME_OVERRIDE:
        return DISPLAY_NAME_OVERRIDE[team_name]
    import textwrap
    return "\n".join(textwrap.wrap(team_name, width=wrap_width, break_long_words=False)) or team_name


# Host nation(s) per edition, for the year-selector sidebar. Names match
# FLAG_CODE keys so historical_teams.flag_code() resolves the right icon.
# Co-hosted editions (2002, 2026) list every host.
HOST_COUNTRIES = {
    1930: ("Uruguay",), 1934: ("Italy",), 1938: ("France",),
    1950: ("Brazil",), 1954: ("Switzerland",), 1958: ("Sweden",),
    1962: ("Chile",), 1966: ("England",), 1970: ("Mexico",),
    1974: ("West Germany",), 1978: ("Argentina",), 1982: ("Spain",),
    1986: ("Mexico",), 1990: ("Italy",), 1994: ("United States",),
    1998: ("France",), 2002: ("South Korea", "Japan"), 2006: ("Germany",),
    2010: ("South Africa",), 2014: ("Brazil",), 2018: ("Russia",),
    2022: ("Qatar",), 2026: ("United States", "Canada", "Mexico"),
}


def host_countries(year: int) -> tuple[str, ...]:
    return HOST_COUNTRIES.get(year, ())


# Some historical name variants intentionally reuse a related federation's
# crest file rather than maintaining their own: "United States" (used in
# 1930/1934/1950/1990 data) is the same federation as "USA" (1994+), and
# "West Germany" (1954-1990) is treated as the same federation as "Germany"
# for crest purposes.
CREST_NAME_ALIAS = {
    "United States": "USA",
    "West Germany": "Germany",
}


def crest_source_name(team_name: str) -> str:
    """Team name whose crest file should actually be used for team_name."""
    return CREST_NAME_ALIAS.get(team_name, team_name)


def list_all_countries() -> dict[str, str]:
    """{ISO 3166-1 alpha-2 flag code: canonical team name}, across every team
    that has ever appeared (bracket or group stage) in any downloaded edition
    in ALL_YEARS. Requires worldcup_data/ to already be populated; editions
    that aren't on disk yet are silently skipped, so run
    download_resources.download_all_worldcup_data() first for full coverage.

    A handful of codes are historical, not current ISO entries (su, yu, zr,
    cs, dd for defunct nations; the four UK home nations use gb-eng/gb-sct/
    gb-wls/gb-nir) -- see FLAG_CODE above, the source of this name-to-code
    mapping. Later editions' spelling wins when the same country's name
    changed over time (e.g. an accent or "and"/"&" convention).
    """
    name_by_code: dict[str, str] = {}
    for year in ALL_YEARS:
        source = year_source(year)
        if not source.exists():
            continue
        data = load_worldcup_json(source)
        for match in data["matches"]:
            for key in ("team1", "team2"):
                name = match.get(key)
                if not isinstance(name, str):
                    continue
                code = flag_code(name)
                if code:
                    name_by_code[code] = name
    return name_by_code
