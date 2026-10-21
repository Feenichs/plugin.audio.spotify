"""
    plugin.audio.spotify
    lyrics.py
    Lyrics of the playing track for the TV interface. This module has no Kodi
    dependencies.

    Sources, in this order:
    - LRCLIB (https://lrclib.net), free and open, mostly time-synced (LRC)
      lyrics: first the exact lookup, then the search, which also finds tracks
      whose album or duration differs a little.
    - lyrics.ovh (https://lyrics.ovh), free, plain lyrics only.
    Spotify's own lyrics are only available through an internal, unofficial
    API, so they are not used.
"""

import bisect
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

USER_AGENT = "plugin.audio.spotify TV interface (https://github.com/Feenichs/plugin.audio.spotify)"
TIMEOUT_SECS = 10
# LRCLIB answers "ServerOverloaded" when busy
RETRIES = 2
RETRY_PAUSE_SECS = 1.5
# a track whose lyrics were not found is looked up again after a day
NOT_FOUND_EXPIRY_SECS = 24 * 60 * 60
# Results are incomplete if a source failed, e.g. LRCLIB was overloaded and
# only plain lyrics of lyrics.ovh were found. They are looked up again later.
INCOMPLETE_EXPIRY_SECS = 60
MAX_DURATION_DIFF_SECS = 4
# an instrumental intro is shown as an empty line, as in the Spotify app
INTRO_MIN_SECS = 3

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
LYRICS_OVH_URL = "https://api.lyrics.ovh/v1/{artist}/{title}"

LRC_LINE_RE = re.compile(r"^((?:\[\d+:\d+(?:[.:]\d+)?\])+)(.*)$")
LRC_TIME_RE = re.compile(r"\[(\d+):(\d+)(?:[.:](\d+))?\]")
# " - Remastered 2011", " (feat. X)", " [Live]" etc. are not part of the titles
# in the lyrics databases
TITLE_SUFFIX_RE = re.compile(
    r"\s+(?:-\s+.*|\((?:feat|ft|with|remaster|live|radio|single|mono|stereo|version|edit)[^)]*\)"
    r"|\[[^\]]*\])$",
    re.IGNORECASE,
)


class Lyrics:
    def __init__(self, lines: List[Tuple[float, str]], plain: str, source: str):
        # time-synced lines, (seconds, text), empty if only plain lyrics exist
        if lines and lines[0][0] >= INTRO_MIN_SECS:
            lines = [(0.0, "")] + list(lines)
        self.lines = lines
        self.plain = plain
        self.source = source

    @property
    def is_synced(self) -> bool:
        return bool(self.lines)

    def text_lines(self) -> List[str]:
        if self.lines:
            return [text for _, text in self.lines]
        return self.plain.splitlines()

    def line_at(self, position: float) -> int:
        """Index of the synced line sung at the position (seconds), -1 before the first"""
        return bisect.bisect_right([start for start, _ in self.lines], position) - 1

    def to_json(self) -> Dict[str, Any]:
        return {"lines": self.lines, "plain": self.plain, "source": self.source}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Lyrics":
        return cls([tuple(line) for line in data.get("lines") or []],
                   data.get("plain") or "",
                   data.get("source") or "")


def parse_lrc(lrc: str) -> List[Tuple[float, str]]:
    lines = []
    for raw_line in (lrc or "").splitlines():
        match = LRC_LINE_RE.match(raw_line.strip())
        if not match:
            continue
        text = match.group(2).strip()
        for minutes, seconds, fraction in LRC_TIME_RE.findall(match.group(1)):
            start = int(minutes) * 60 + int(seconds)
            if fraction:
                start += int(fraction) / (10 ** len(fraction))
            lines.append((float(start), text))
    lines.sort(key=lambda line: line[0])
    return lines


def main_artist(artist: str) -> str:
    """The plugin joins the artists of a track with " / " """
    return re.split(r"\s+/\s+", artist or "", maxsplit=1)[0].strip()


def clean_title(title: str) -> str:
    previous = None
    title = (title or "").strip()
    while title != previous:
        previous = title
        title = TITLE_SUFFIX_RE.sub("", title).strip()
    return title


def _request(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """GET a JSON document, None if not found"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in (429, 502, 503, 504) or attempt == RETRIES - 1:
                raise
        time.sleep(RETRY_PAUSE_SECS * (attempt + 1))
    return None


def _from_lrclib(record: Dict[str, Any]) -> Optional[Lyrics]:
    if not isinstance(record, dict) or record.get("instrumental"):
        return None
    lines = parse_lrc(record.get("syncedLyrics") or "")
    plain = record.get("plainLyrics") or ""
    if not lines and not plain:
        return None
    return Lyrics(lines, plain, "LRCLIB")


def _lrclib_get(artist: str, title: str, album: str, duration: int) -> Optional[Lyrics]:
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = duration
    return _from_lrclib(_request(LRCLIB_GET_URL, params))


def _lrclib_search(artist: str, title: str, duration: int) -> Optional[Lyrics]:
    results = _request(LRCLIB_SEARCH_URL, {"artist_name": artist, "track_name": title})
    if not isinstance(results, list) or not results:
        results = _request(LRCLIB_SEARCH_URL, {"q": f"{artist} {title}"})
    if not isinstance(results, list):
        return None

    def matches(record):
        if duration and record.get("duration"):
            if abs(float(record["duration"]) - duration) > MAX_DURATION_DIFF_SECS:
                return False
        return artist.lower() in (record.get("artistName") or "").lower()

    candidates = [record for record in results if matches(record)]
    # synced lyrics first, the order of the search results otherwise
    candidates.sort(key=lambda record: not record.get("syncedLyrics"))
    for record in candidates:
        lyrics = _from_lrclib(record)
        if lyrics:
            return lyrics
    return None


def _lyrics_ovh(artist: str, title: str) -> Optional[Lyrics]:
    url = LYRICS_OVH_URL.format(
        artist=urllib.parse.quote(artist, safe=""), title=urllib.parse.quote(title, safe="")
    )
    result = _request(url)
    plain = (result or {}).get("lyrics") or ""
    # lyrics.ovh separates paragraphs with blank lines, and sometimes
    # starts with a "Paroles de la chanson ..." line
    plain = re.sub(r"^Paroles de la chanson.*\n", "", plain).strip()
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return Lyrics([], plain, "lyrics.ovh") if plain else None


def fetch(
    artist: str,
    title: str,
    album: str = "",
    duration: float = 0,
    log: Callable[[str], None] = lambda msg: None,
) -> Tuple[Optional[Lyrics], bool]:
    """
    Look up the lyrics of a track in the sources. LRCLIB does not allow
    parallel requests, so calls must not run in parallel.

    :return: (lyrics or None if no source has them, whether all sources
             answered, otherwise a later lookup may find better lyrics)
    """
    artist = main_artist(artist)
    title = (title or "").strip()
    short_title = clean_title(title)
    duration = int(round(duration or 0))
    if not artist or not title:
        return None, True

    lrclib_lookups = [lambda: _lrclib_get(artist, title, album, duration)]
    if short_title and short_title != title:
        lrclib_lookups.append(lambda: _lrclib_get(artist, short_title, album, duration))
    lrclib_lookups.append(lambda: _lrclib_search(artist, short_title or title, duration))

    errors = []
    for lookup in lrclib_lookups:
        try:
            lyrics = lookup()
        except (OSError, ValueError) as exc:
            # LRCLIB is down or overloaded, use the next source for now
            log(f"LRCLIB lookup of '{artist} - {title}' failed: {exc}")
            errors.append(exc)
            break
        if lyrics:
            return lyrics, True

    try:
        lyrics = _lyrics_ovh(artist, short_title or title)
    except (OSError, ValueError) as exc:
        log(f"lyrics.ovh lookup of '{artist} - {title}' failed: {exc}")
        if errors:
            raise errors[0]
        return None, False
    return lyrics, not errors


class LyricsCache:
    """Lyrics per track, as JSON files. Tracks without lyrics are retried after a day."""

    def __init__(self, directory: str):
        self.__directory = directory

    def __path(self, key: str) -> str:
        return os.path.join(self.__directory, re.sub(r"[^A-Za-z0-9_-]", "_", key) + ".json")

    def get(self, key: str) -> Tuple[bool, Optional[Lyrics], bool]:
        """
        (found in the cache, lyrics or None if the track has none, whether
        the lyrics should be looked up again as they may be incomplete)
        """
        try:
            with open(self.__path(key), encoding="utf-8") as cache_file:
                data = json.load(cache_file)
        except (OSError, ValueError):
            return False, None, True
        age = time.time() - data.get("time", 0)
        if data.get("incomplete"):
            stale = age > INCOMPLETE_EXPIRY_SECS
        else:
            stale = bool(data.get("not_found")) and age > NOT_FOUND_EXPIRY_SECS
        if data.get("not_found"):
            return not stale, None, stale
        return True, Lyrics.from_json(data), stale

    def set(self, key: str, lyrics: Optional[Lyrics], complete: bool = True) -> None:
        data = lyrics.to_json() if lyrics else {"not_found": True}
        data["time"] = time.time()
        if not complete:
            data["incomplete"] = True
        try:
            os.makedirs(self.__directory, exist_ok=True)
            with open(self.__path(key), "w", encoding="utf-8") as cache_file:
                json.dump(data, cache_file)
        except OSError:
            pass
