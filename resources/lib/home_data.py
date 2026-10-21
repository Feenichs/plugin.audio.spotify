"""
    plugin.audio.spotify
    home_data.py
    Data handling of the TV interface (home window). The home window lists the
    plugin's directories via JSON-RPC and arranges them in tabs and rows.
    This module has no Kodi dependencies.
"""

import json
import urllib.parse
from typing import Any, Dict, List, Optional

ADDON_ID = "plugin.audio.spotify"
PLUGIN_URL = f"plugin://{ADDON_ID}/"
ADDON_MEDIA = f"/addons/{ADDON_ID}/resources/"

# Marks the root menu request of the classic menu, which otherwise opens the
# home window when the add-on is started.
CLASSIC_MENU = "classic"

TAB_HOME = "home"
TAB_LIBRARY = "library"
TAB_EXPLORE = "explore"
TAB_SEARCH = "search"
TAB_ACCOUNT = "account"
TABS = (TAB_HOME, TAB_LIBRARY, TAB_EXPLORE, TAB_SEARCH, TAB_ACCOUNT)

ROW_FEED = "feed"
ROW_ACTIONS = "actions"
ROW_RECENT = "recent"

# Plugin actions, i.e. the method names of PluginContent
BROWSE_PLAYLISTS = "browse_playlists"
BROWSE_PLAYLIST = "browse_playlist"
BROWSE_ALBUM = "browse_album"
BROWSE_CATEGORY = "browse_category"
BROWSE_NEW_RELEASES = "browse_new_releases"
BROWSE_SAVED_ALBUMS = "browse_saved_albums"
BROWSE_SAVED_TRACKS = "browse_saved_tracks"
BROWSE_SAVED_ARTISTS = "browse_saved_artists"
BROWSE_FOLLOWED_ARTISTS = "browse_followed_artists"
BROWSE_TOP_ARTISTS = "browse_top_artists"
BROWSE_TOP_TRACKS = "browse_top_tracks"
BROWSE_ARTIST = "browse_artist_just_albums_and_singles"
BROWSE_ARTIST_ALBUMS = "browse_artist_just_albums"
BROWSE_ARTIST_SINGLES = "browse_artist_just_singles"
BROWSE_ARTIST_APPEARS_ON = "browse_artist_just_appears_on"
ARTIST_TOP_TRACKS = "artist_top_tracks"
RELATED_ARTISTS = "related_artists"
BROWSE_PLAYLIST_TAIL = "browse_playlist_tail"
PLAY_PLAYLIST = "play_playlist"
REFRESH_LISTING = "refresh_listing"
SEARCH_TRACKS = "search_tracks"
SEARCH_ARTISTS = "search_artists"
SEARCH_ALBUMS = "search_albums"
SEARCH_PLAYLISTS = "search_playlists"
CHECK_AUTHENTICATION = "check_authentication"
AUTHENTICATE = "authenticate_plugin_request"
DELETE_CACHE = "delete_cache_db"

# Properties requested for every listed item
LIST_PROPERTIES = (
    "title",
    "artist",
    "album",
    "year",
    "genre",
    "duration",
    "track",
    "art",
    "thumbnail",
    "fanart",
    "customproperties",
)

# Longer listings are cut off in rows, they are shown in full via "show all"
ROW_MAX_ITEMS = 40
RECENT_MAX_ITEMS = 20
# Number of category rows on the explore tab, all categories are tiles
EXPLORE_CATEGORY_ROWS = 4
SEARCH_HISTORY_SIZE = 12


def create_uri(action: str = "", **params: str) -> str:
    query = {"action": action} if action else {}
    query.update(params)
    if not query:
        return PLUGIN_URL
    return PLUGIN_URL + "?" + urllib.parse.urlencode(query)


def root_uri() -> str:
    return create_uri(**{CLASSIC_MENU: "true"})


def uri_params(uri: str) -> Dict[str, str]:
    if not uri or not uri.startswith(PLUGIN_URL):
        return {}
    query = urllib.parse.urlsplit(uri).query
    return dict(urllib.parse.parse_qsl(query, keep_blank_values=True))


def uri_action(uri: str) -> str:
    return uri_params(uri).get("action", "").lower()


def search_uris(query: str) -> Dict[str, str]:
    return {
        SEARCH_TRACKS: create_uri(SEARCH_TRACKS, trackid=query),
        SEARCH_ARTISTS: create_uri(SEARCH_ARTISTS, artistid=query),
        SEARCH_ALBUMS: create_uri(SEARCH_ALBUMS, albumid=query),
        SEARCH_PLAYLISTS: create_uri(SEARCH_PLAYLISTS, playlistid=query),
    }


def decode_image(url: str) -> str:
    """Kodi JSON-RPC returns art as image://<url encoded path>/"""
    if url and url.startswith("image://"):
        return urllib.parse.unquote(url[len("image://") :].rstrip("/"))
    return url or ""


def format_duration(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


def build_request(uri: str, properties=LIST_PROPERTIES) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "Files.GetDirectory",
        "params": {
            "directory": uri,
            "media": "music",
            "properties": list(properties),
        },
    }


def parse_response(response: Any) -> List[Dict[str, Any]]:
    if not response:
        return []
    if not isinstance(response, dict):
        try:
            response = json.loads(response)
        except ValueError:
            return []
    result = response.get("result") or {}
    return result.get("files") or []


def _join(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, (list, tuple)):
        return " / ".join(str(value) for value in values if value)
    return str(values)


def _is_addon_image(image: str) -> bool:
    image = decode_image(image)
    return ADDON_MEDIA in image or image.startswith("Default")


def track_id_from_uri(uri: str) -> str:
    """Tracks are played via the proxy of the service: <proxy>/track/<id>/<duration>"""
    parts = urllib.parse.urlsplit(uri)
    if parts.scheme != "http" or parts.hostname != "localhost":
        return ""
    segments = parts.path.strip("/").split("/")
    if len(segments) >= 2 and segments[0] == "track":
        return segments[1]
    return ""


def item_kind(uri: str, is_folder: bool, props: Dict[str, str]) -> str:
    """Kind of a listed item: track, album, playlist, artist, category or folder"""
    if not is_folder:
        if props.get("spotifytrackid") or props.get("isplayable") == "true":
            return "track"
        return "action"
    action = uri_action(uri)
    if action == BROWSE_ALBUM:
        return "album"
    if action == BROWSE_PLAYLIST:
        return "playlist"
    if action == BROWSE_CATEGORY:
        return "category"
    if action.startswith("browse_artist_"):
        return "artist"
    return "folder"


def normalise_item(raw: Dict[str, Any], base_uri: Optional[str] = None) -> Dict[str, Any]:
    """Convert a JSON-RPC file entry to the dict used by the home window"""
    props = {}
    context_menu = {}
    for key, value in (raw.get("customproperties") or {}).items():
        key = key.lower()
        if key.startswith(("contextmenulabel(", "contextmenuaction(")):
            kind, _, index = key[:-1].partition("(")
            try:
                index = int(index)
            except ValueError:
                continue
            context_menu.setdefault(index, {})[kind] = value
        else:
            props[key] = value

    uri = raw.get("file", "")
    is_folder = raw.get("filetype") == "directory"
    if not is_folder and "spotifytrackid" not in props:
        track_id = track_id_from_uri(uri)
        if track_id:
            props["spotifytrackid"] = track_id
    art = raw.get("art") or {}
    thumb = art.get("thumb") or raw.get("thumbnail") or art.get("icon") or ""
    kind = item_kind(uri, is_folder, props)

    # the "next page" entry of search results links to the same listing with
    # an offset
    is_next_page = (
        is_folder
        and base_uri is not None
        and uri_action(uri) == uri_action(base_uri)
        and "offset" in uri_params(uri)
    )

    if kind == "artist":
        tile = "round"
    elif not thumb or _is_addon_image(thumb):
        tile = "icon"
    else:
        tile = "square"

    label = raw.get("label") or raw.get("title") or ""
    if kind == "track":
        # playlists label tracks with "artist - title", the artist is shown
        # separately
        label = raw.get("title") or label
    artist = _join(raw.get("artist"))
    album = raw.get("album") or ""
    year = raw.get("year") or 0
    if year <= 1900:
        # the plugin's placeholder for tracks without album
        year = 0
    genre = _join(raw.get("genre"))
    duration = raw.get("duration") or 0

    if kind == "track":
        subtitle = artist
        info_line = " · ".join(
            part for part in (album, str(year) if year else "", format_duration(duration)) if part
        )
    elif kind == "album":
        subtitle = " · ".join(part for part in (artist, str(year) if year else "") if part)
        info_line = genre
    elif kind == "artist":
        subtitle = genre.split(" / ")[0] if genre else ""
        info_line = genre
    elif kind == "playlist":
        # the plugin sets the owner of a playlist as its artist
        subtitle = artist
        info_line = ""
    else:
        subtitle = ""
        info_line = ""

    return {
        "label": label,
        "title": raw.get("title") or label,
        "uri": uri,
        "kind": kind,
        "tile": tile,
        "thumb": thumb,
        "fanart": thumb if tile != "icon" else "",
        "artist": artist,
        "album": album,
        "year": year,
        "genre": genre,
        "duration": duration,
        "duration_label": format_duration(duration),
        "track_number": raw.get("track") or 0,
        "subtitle": subtitle,
        "info_line": info_line,
        "is_folder": is_folder,
        "is_track": kind == "track",
        "is_next_page": is_next_page,
        "properties": props,
        "context_menu": [
            (entry.get("contextmenulabel", ""), entry.get("contextmenuaction", ""))
            for _, entry in sorted(context_menu.items())
        ],
    }


def make_item(**kwargs: Any) -> Dict[str, Any]:
    """Create an item that does not come from a plugin listing, e.g. a tile"""
    item = {
        "label": "",
        "title": "",
        "uri": "",
        "kind": "folder",
        "tile": "icon",
        "thumb": "",
        "fanart": "",
        "artist": "",
        "album": "",
        "year": 0,
        "genre": "",
        "duration": 0,
        "duration_label": "",
        "track_number": 0,
        "subtitle": "",
        "info_line": "",
        "is_folder": False,
        "is_track": False,
        "is_next_page": False,
        "properties": {},
        "context_menu": [],
    }
    item.update(kwargs)
    if not item["title"]:
        item["title"] = item["label"]
    return item


def content_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for item in items if not item["is_next_page"]]


def recent_tracks(items: List[Dict[str, Any]], max_items: int = RECENT_MAX_ITEMS):
    """
    Tracks of the "recently played" playlist, which the service appends played
    tracks to: most recent first and without repetitions
    """
    seen = set()
    result = []
    for item in reversed(items):
        track_id = item["properties"].get("spotifytrackid")
        if not item["is_track"] or not track_id or track_id in seen:
            continue
        seen.add(track_id)
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def played_track_item(track: Dict[str, Any]) -> Dict[str, Any]:
    """Item of a track of the local playback history, see merge_recent"""
    duration = track.get("duration") or 0
    album = track.get("album") or ""
    return make_item(
        label=track.get("title") or "",
        uri=track.get("uri") or "",
        kind="track",
        tile="square",
        thumb=track.get("thumb") or "",
        fanart=track.get("thumb") or "",
        artist=track.get("artist") or "",
        album=album,
        duration=duration,
        duration_label=format_duration(duration),
        subtitle=track.get("artist") or "",
        info_line=" · ".join(part for part in (album, format_duration(duration)) if part),
        is_track=True,
        properties={"spotifytrackid": track.get("id") or ""},
    )


def merge_recent(
    local: List[Dict[str, Any]], playlist: List[Dict[str, Any]], max_items: int = RECENT_MAX_ITEMS
) -> List[Dict[str, Any]]:
    """
    Tracks played on this device first, as the recently played playlist is only
    updated when a track has finished, then those of the playlist. Tracks of
    the playlist replace the local ones, they have the full context menu.
    """
    by_id = {item["properties"].get("spotifytrackid"): item for item in playlist}
    result = []
    seen = set()
    for item in local + playlist:
        track_id = item["properties"].get("spotifytrackid")
        if not track_id or track_id in seen:
            continue
        seen.add(track_id)
        result.append(by_id.get(track_id, item))
        if len(result) >= max_items:
            break
    return result


def find_playlist(items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    for item in items:
        if item["kind"] == "playlist" and item["label"] == name:
            return item
    return None


def _feed(title: str, uri: str, **kwargs: Any) -> Dict[str, Any]:
    row = {"type": ROW_FEED, "title": title, "uri": uri}
    row.update(kwargs)
    return row


def build_tabs(
    library_items: List[Dict[str, Any]],
    explore_items: List[Dict[str, Any]],
    labels: Dict[str, str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Arrange the entries of the classic "My music" and "Explore" menus in the
    tabs of the home window. Every entry ends up in at least one tab, so no
    feature of the classic menu is lost.

    :param labels: localized row titles, keyed by the plugin action
    :return: dict of tab id to list of row dicts
    """
    tabs = {tab: [] for tab in TABS}

    library = {}
    library_rows = []
    for item in library_items:
        action = uri_action(item["uri"])
        if not item["is_folder"] or not action:
            continue
        library.setdefault(action, item)
        library_rows.append(_feed(item["label"], item["uri"]))

    featured_uri = create_uri(BROWSE_PLAYLISTS, applyfilter="featured")
    new_releases_uri = create_uri(BROWSE_NEW_RELEASES)
    categories = []
    for item in explore_items:
        action = uri_action(item["uri"])
        params = uri_params(item["uri"])
        if action == BROWSE_PLAYLISTS and params.get("applyfilter") == "featured":
            featured_uri = item["uri"]
        elif action == BROWSE_NEW_RELEASES:
            new_releases_uri = item["uri"]
        elif item["is_folder"]:
            categories.append(item)

    def _library(action, title=None):
        item = library.get(action)
        if not item:
            return None
        return _feed(title or item["label"], item["uri"])

    playlists = library.get(BROWSE_PLAYLISTS)
    home = [
        {
            "type": ROW_RECENT,
            "title": labels.get(ROW_RECENT),
            "uri": playlists["uri"] if playlists else None,
        },
        _library(BROWSE_PLAYLISTS, labels.get(BROWSE_PLAYLISTS)),
        _library(BROWSE_TOP_TRACKS),
        _feed(labels.get(BROWSE_NEW_RELEASES, ""), new_releases_uri),
        _library(BROWSE_TOP_ARTISTS),
        _library(BROWSE_SAVED_ALBUMS, labels.get(BROWSE_SAVED_ALBUMS)),
        _feed(labels.get("featured", ""), featured_uri),
    ]
    tabs[TAB_HOME] = [row for row in home if row]

    tabs[TAB_LIBRARY] = library_rows

    explore = [
        _feed(labels.get("featured", ""), featured_uri),
        _feed(labels.get(BROWSE_NEW_RELEASES, ""), new_releases_uri),
    ]
    if categories:
        explore.append(
            {
                "type": ROW_ACTIONS,
                "title": labels.get(BROWSE_CATEGORY),
                "items": categories,
            }
        )
        explore += [
            _feed(item["label"], item["uri"]) for item in categories[:EXPLORE_CATEGORY_ROWS]
        ]
    tabs[TAB_EXPLORE] = explore
    return tabs


def search_rows(query: str, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    uris = search_uris(query)
    return [
        _feed(labels[action], uris[action])
        for action in (SEARCH_TRACKS, SEARCH_ARTISTS, SEARCH_ALBUMS, SEARCH_PLAYLISTS)
    ]


def add_search_history(history: List[str], query: str) -> List[str]:
    history = [entry for entry in history if entry.lower() != query.lower()]
    return ([query] + history)[:SEARCH_HISTORY_SIZE]


def context_menu_entries(item: Dict[str, Any]) -> List[tuple]:
    """Return the (label, action) pairs of the item's context menu"""
    return [
        (label, action)
        for label, action in item.get("context_menu") or ()
        if label and action and uri_action(_call_args(action)) != PLAY_PLAYLIST
    ]


def _call_args(action: str) -> str:
    """The first argument of a builtin like RunPlugin(uri) or Container.Update(uri)"""
    start = action.find("(")
    if start < 0 or not action.endswith(")"):
        return ""
    return action[start + 1 : -1].split(",")[0].strip().strip('"')


def navigation_target(action: str) -> Optional[str]:
    """
    Return the plugin uri if the action only navigates to a plugin listing,
    so it can be shown inside the home window instead of the classic list.
    """
    action = (action or "").strip()
    name = action.split("(")[0].strip().lower()
    if name == "container.update":
        uri = _call_args(action)
    elif name == "activatewindow":
        args = action[action.find("(") + 1 : -1].split(",")
        if len(args) < 2 or args[0].strip().lower() not in {"music", "10502"}:
            return None
        uri = args[1].strip().strip('"')
    else:
        return None
    if not uri.startswith(PLUGIN_URL):
        return None
    return uri


def plugin_action(action: str) -> str:
    """The plugin action of a RunPlugin(...) builtin"""
    if not action.strip().lower().startswith("runplugin("):
        return ""
    return uri_action(_call_args(action.strip()))


def artist_rows(artist_id: str, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    """Rows of an artist page"""
    return [
        _feed(labels[action], create_uri(action, artistid=artist_id))
        for action in (
            ARTIST_TOP_TRACKS,
            BROWSE_ARTIST_ALBUMS,
            BROWSE_ARTIST_SINGLES,
            BROWSE_ARTIST_APPEARS_ON,
            RELATED_ARTISTS,
        )
    ]


def tracks_summary(items: List[Dict[str, Any]], tracks_label: str) -> str:
    tracks = [item for item in items if item["is_track"]]
    total = sum(int(item["duration"] or 0) for item in tracks)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    length = f"{hours} h {minutes} min" if hours else f"{minutes} min"
    return f"{len(tracks)} {tracks_label} · {length}"
