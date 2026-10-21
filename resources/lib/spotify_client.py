"""
    plugin.audio.spotify
    spotify_client.py
    Spotify Web API client for the user's own client id, with a fallback to the
    add-on's shared client id.

    Spotify throttles requests per client id (app). The add-on's client id is
    shared by all its users, so requests are often throttled for many seconds.
    With an own client id, requests are only throttled by the user's own
    usage.

    But apps in development mode, which own client ids are, may only use part
    of the Web API since February 2026, and get some results in a different
    format: https://developer.spotify.com/documentation/web-api/references/changes/february-2026
    So only the requests listed in OWN_CLIENT_METHODS use the own client id,
    and their results are converted to the format of the shared client id.
    Everything else, and every request the own client id may not make, uses
    the shared client id.
"""

from typing import Any

import spotipy

import utils
from utils import log_msg

# Requests that apps in development mode may still make, for the user's own
# library and own playlists. Spotify's and other users' playlists only return
# metadata, those are listed with the shared client id.
OWN_CLIENT_METHODS = {
    "album",
    "album_tracks",
    "artist_albums",
    "current_user_followed_artists",
    "current_user_playlists",
    "current_user_saved_albums",
    "current_user_saved_tracks",
    "current_user_top_artists",
    "current_user_top_tracks",
    "next",
    "playlist",
    "playlist_items",
}

# Status codes of requests the own client id may not make
FALLBACK_STATUS_CODES = (400, 401, 403, 404, 410)


def _to_shared_format(value: Any) -> Any:
    """Convert a result of the own client id to the format of the shared one"""
    if isinstance(value, list):
        return [_to_shared_format(entry) for entry in value]
    if not isinstance(value, dict):
        return value

    value = {key: _to_shared_format(entry) for key, entry in value.items()}
    kind = value.get("type")
    # playlists: "tracks" was renamed to "items"
    if kind == "playlist" and isinstance(value.get("items"), dict) and "tracks" not in value:
        value["tracks"] = value.pop("items")
    # playlist entries: "track" was renamed to "item", "track" is a boolean now
    if isinstance(value.get("item"), dict) and not isinstance(value.get("track"), dict):
        value["track"] = value.pop("item")
    # removed fields, "genres" is not returned either (not documented)
    if kind in ("track", "album", "artist"):
        value.setdefault("popularity", 0)
    if kind in ("album", "artist"):
        value.setdefault("genres", [])
    if kind == "artist":
        value.setdefault("followers", {"total": 0})
    return value


class FallbackSpotify:
    def __init__(self, own: spotipy.Spotify, shared: spotipy.Spotify):
        self.__own = own
        self.__shared = shared
        # playlists the own client id may not list, e.g. other users' ones,
        # their further pages are listed with the shared client id directly
        self.__shared_playlists = set()

    def __getattr__(self, name: str):
        if name not in OWN_CLIENT_METHODS:
            return getattr(self.__shared, name)

        def call(*args, **kwargs):
            try:
                result = self.__call_own(name, *args, **kwargs)
            except spotipy.SpotifyException as exc:
                if exc.http_status not in FALLBACK_STATUS_CODES:
                    raise
                log_msg(
                    f"'{name}' failed with the own client id ({exc.http_status}),"
                    f" using the shared client id."
                )
                if name in ("playlist", "playlist_items"):
                    self.__shared_playlists.add(self.__playlist_id(*args, **kwargs))
                result = None
            if result is None:
                result = getattr(self.__shared, name)(*args, **kwargs)
            return result

        return call

    def __call_own(self, name: str, *args, **kwargs) -> Any:
        """Result of the own client id, None if the shared client id must be used"""
        if name == "playlist":
            return self.__playlist(*args, **kwargs)
        if name == "playlist_items":
            return self.__playlist_items(*args, **kwargs)
        return _to_shared_format(getattr(self.__own, name)(*args, **kwargs))

    def __playlist_id(self, playlist_id, *args, **kwargs) -> str:
        return self.__own._get_id("playlist", playlist_id)

    def __playlist(self, playlist_id, fields=None, market=None, **kwargs):
        if self.__playlist_id(playlist_id) in self.__shared_playlists:
            return None
        if fields:
            fields = fields.replace("tracks", "items")
        playlist = _to_shared_format(
            self.__own.playlist(playlist_id, fields=fields, market=market, **kwargs)
        )
        # The tracks are only returned for the user's own playlists.
        # Converted here too, as the type is not returned if fields are selected.
        if "tracks" not in playlist and isinstance(playlist.get("items"), dict):
            playlist["tracks"] = playlist.pop("items")
        if "tracks" not in playlist:
            self.__shared_playlists.add(self.__playlist_id(playlist_id))
            return None
        return playlist

    def __playlist_items(
        self, playlist_id, fields=None, limit=100, offset=0, market=None, **kwargs
    ):
        if self.__playlist_id(playlist_id) in self.__shared_playlists:
            return None
        # the "tracks" endpoint was renamed to "items"
        if fields:
            fields = fields.replace("track", "item")
        return _to_shared_format(
            self.__own._get(
                f"playlists/{self.__own._get_id('playlist', playlist_id)}/items",
                limit=limit,
                offset=offset,
                fields=fields,
                market=market,
                additional_types="track,episode",
            )
        )


def get_spotify(shared_auth_token: str) -> spotipy.Spotify:
    """Client for the own client id if there is one, otherwise the shared one"""
    shared = spotipy.Spotify(auth=shared_auth_token)
    own_auth_token = utils.get_own_auth_token()
    if not own_auth_token:
        return shared
    return FallbackSpotify(spotipy.Spotify(auth=own_auth_token), shared)
