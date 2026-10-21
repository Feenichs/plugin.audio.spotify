"""
    plugin.audio.spotify
    home_window.py
    TV interface: a full-screen window with tabs and rows of tiles, made for
    remote controls. The content is listed from the plugin via JSON-RPC.
"""

import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import xbmc
import xbmcaddon
import xbmcgui

import home_data as data
import utils
from string_ids import *
from utils import ADDON_ID, log_exception, log_msg

ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_CONTEXT_MENU = 117
ACTION_MOUSE_LONG_CLICK = 108

NAV_LIST = 100
ROWS_GROUPLIST = 300
ROW_LISTS = tuple(range(301, 309))
ROW_LABELS = tuple(range(401, 409))
ROW_GROUPS = tuple(range(501, 509))
GRID_PANEL = 600
TRACK_LIST = 700
TRACK_BUTTONS = 710
ITEM_LISTS = ROW_LISTS + (GRID_PANEL, TRACK_LIST)

CACHE_SECONDS = 300
# items per grid page, independent of the page size of the listings
GRID_PAGE_SIZE = 30
# track lists are not paged, but very long listings are cut off
TRACK_LIST_MAX_ITEMS = 1000
TAB_SWITCH_DELAY = 0.35
# rows are listed in parallel, each listing is a separate plugin call
ROW_WORKERS = 3
AUTH_WAIT_SECONDS = 10

HOME_WINDOW_ACTIVE = "spotify_home_window_active"
SEARCH_HISTORY_FILE = os.path.join(utils.ADDON_DATA_PATH, "search_history.json")

TAB_LABELS = {
    data.TAB_HOME: (HOME_WINDOW_TAB_HOME_STR_ID, HOME_WINDOW_TAB_HOME_DESCRIPTION_STR_ID),
    data.TAB_LIBRARY: (HOME_WINDOW_TAB_LIBRARY_STR_ID, HOME_WINDOW_TAB_LIBRARY_DESCRIPTION_STR_ID),
    data.TAB_EXPLORE: (HOME_WINDOW_TAB_EXPLORE_STR_ID, HOME_WINDOW_TAB_EXPLORE_DESCRIPTION_STR_ID),
    data.TAB_SEARCH: (HOME_WINDOW_TAB_SEARCH_STR_ID, HOME_WINDOW_TAB_SEARCH_DESCRIPTION_STR_ID),
    data.TAB_ACCOUNT: (HOME_WINDOW_TAB_ACCOUNT_STR_ID, HOME_WINDOW_TAB_ACCOUNT_DESCRIPTION_STR_ID),
}


class _PlayerMonitor(xbmc.Player):
    """Tells the window when the playing track changes"""

    def __init__(self, on_change):
        super().__init__()
        self.__on_change = on_change

    def onAVStarted(self):
        self.__on_change()

    def onPlayBackStopped(self):
        self.__on_change()

    def onPlayBackEnded(self):
        self.__on_change()


class HomeWindow(xbmcgui.WindowXML):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__addon = xbmcaddon.Addon(id=ADDON_ID)
        self.__home = xbmcgui.Window(utils.ADDON_WINDOW_ID)
        self.__addon_path = self.__addon.getAddonInfo("path")
        self.__lock = threading.RLock()
        self.__generation = 0
        self.__cache = {}
        # one lock per listing, rows listed in parallel can share a listing
        self.__listing_locks: Dict[str, threading.Lock] = {}
        self.__tabs: Dict[str, List[Dict[str, Any]]] = {}
        self.__tab_ids: List[str] = []
        self.__current_tab: Optional[str] = None
        self.__tab_timer = None
        # pages opened from a tab: grids, track lists and row pages (artists)
        self.__stack: List[Dict[str, Any]] = []
        self.__row_data = {}
        # items of the shown grid or track list
        self.__grid: Optional[Dict[str, Any]] = None
        self.__initialised = False
        self.__closing = False
        self.__restore = None
        self.__focus_rows = False
        self.__focus_from_row = None
        # index of the topmost visible row, rows above it are hidden
        self.__row_offset = 0
        self.__tab_origin = None
        self.__search_query = ""
        self.__signed_in = False
        self.__playing_track_id = ""
        self.__player = _PlayerMonitor(self.__update_playing)

    # Kodi callbacks

    def onInit(self):
        # remember the window id, so that the window can be brought back to
        # the front if it is opened again while it is still running
        self.__home.setProperty(HOME_WINDOW_ACTIVE, str(xbmcgui.getCurrentWindowId()))
        if self.__initialised:
            # returning from another window, e.g. the music visualisation;
            # the content is still shown, only the playing track may differ
            self.__update_playing()
            return
        self.__initialised = True

        self.setProperty("hero", "true" if self.__setting_enabled("home_window_hero") else "false")
        self.setProperty("icon", self.__media_path("icon.png"))
        self.setProperty("view", "tab")
        self.setProperty("empty_label", self.__localize(HOME_WINDOW_EMPTY_STR_ID))
        self.__playing_track_id = self.__current_track_id()
        self.__build_nav()
        self.__load_root()

    def onAction(self, action):
        action_id = action.getId()
        focus_id = self.getFocusId()

        if action_id in (ACTION_NAV_BACK, ACTION_PREVIOUS_MENU):
            self.__on_back(focus_id)
            return

        if action_id in (ACTION_CONTEXT_MENU, ACTION_MOUSE_LONG_CLICK):
            item = self.__focused_item(focus_id)
            if item:
                self.__show_context_menu(focus_id, item)
            return

        if focus_id == NAV_LIST and action_id in (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT):
            self.__schedule_tab_switch()
            return

        if focus_id == NAV_LIST and action_id == ACTION_MOVE_DOWN:
            self.__focus_content()
            return

        if focus_id in ROW_LISTS and action_id in (ACTION_MOVE_UP, ACTION_MOVE_DOWN):
            self.__move_row(ROW_LISTS.index(focus_id), up=action_id == ACTION_MOVE_UP)

    def onClick(self, control_id):
        if control_id == NAV_LIST:
            self.__switch_tab(self.__selected_tab(), focus_rows=True)
            return

        if control_id == TRACK_BUTTONS:
            shuffle = self.getControl(TRACK_BUTTONS).getSelectedPosition() == 1
            items = self.__grid["items"] if self.__grid else []
            self.__play_tracks(items, 0, shuffle=shuffle)
            return

        item = self.__focused_item(control_id)
        if item:
            self.__activate(control_id, item)

    # Navigation

    def __on_back(self, focus_id):
        if self.__stack:
            self.__close_page()
            return
        if focus_id != NAV_LIST:
            self.__set_row_offset(0)
            self.setFocusId(NAV_LIST)
            return
        self.close_window()

    def __focus_content(self):
        view = self.getProperty("view")
        if view == "grid":
            self.setFocusId(GRID_PANEL)
            return
        if view == "tracks":
            self.setFocusId(TRACK_LIST)
            return
        rows = [row for row in self.__filled_rows() if row >= self.__row_offset]
        if rows:
            self.setFocusId(ROW_LISTS[rows[0]])
        else:
            # rows are still loading, focus them once the first is shown
            self.__focus_rows = True

    def __filled_rows(self) -> List[int]:
        return sorted(ROW_LISTS.index(list_id) for list_id in self.__row_data)

    def __set_row_offset(self, offset: int):
        """Show the filled rows from offset on, hide the rows above"""
        self.__row_offset = offset
        for index in self.__filled_rows():
            self.getControl(ROW_GROUPS[index]).setVisible(index >= offset)

    def __move_row(self, index: int, up: bool):
        filled = self.__filled_rows()
        if up:
            above = [row for row in filled if row < index]
            if not above:
                self.__set_row_offset(0)
                self.setFocusId(NAV_LIST)
                return
            target = above[-1]
        else:
            below = [row for row in filled if row > index]
            if not below:
                return
            target = below[0]
        self.__set_row_offset(target)
        self.setFocusId(ROW_LISTS[target])

    def close_window(self):
        self.__closing = True
        with self.__lock:
            self.__generation += 1
        if self.__tab_timer:
            self.__tab_timer.cancel()
        self.close()

    def __build_nav(self):
        fanart = self.__media_path("fanart.jpg")
        nav = self.getControl(NAV_LIST)
        nav.reset()
        items = []
        for tab in data.TABS:
            label, description = TAB_LABELS[tab]
            list_item = xbmcgui.ListItem(
                label=self.__localize(label), label2=self.__localize(description), offscreen=True
            )
            list_item.setArt({"fanart": fanart, "thumb": fanart})
            list_item.setProperty("tab", tab)
            items.append(list_item)
        nav.addItems(items)
        self.__tab_ids = list(data.TABS)

    def __selected_tab(self) -> str:
        position = self.getControl(NAV_LIST).getSelectedPosition()
        if 0 <= position < len(self.__tab_ids):
            return self.__tab_ids[position]
        return data.TAB_HOME

    def __schedule_tab_switch(self):
        if self.__tab_timer:
            self.__tab_timer.cancel()
        timer = threading.Timer(
            TAB_SWITCH_DELAY, lambda: self.__switch_tab(self.__selected_tab())
        )
        timer.daemon = True
        self.__tab_timer = timer
        timer.start()

    def __switch_tab(self, tab: str, focus_rows: bool = False, force: bool = False):
        if self.__closing:
            return
        if self.__stack:
            self.__stack = []
            self.__grid = None
            force = True
        if tab != self.__current_tab or force:
            if tab != self.__current_tab:
                self.__restore = None
            self.__current_tab = tab
            nav = self.getControl(NAV_LIST)
            for position, tab_id in enumerate(self.__tab_ids):
                nav.getListItem(position).setProperty("active", "true" if tab_id == tab else "")
                if tab_id == tab:
                    nav.selectItem(position)
            self.__show_current()
        if focus_rows:
            self.__focus_content()

    # Root and rows

    def __load_root(self, tab: Optional[str] = None):
        self.setProperty("loading", "true")

        def _run():
            self.__signed_in = self.__wait_for_auth()
            self.__update_user()
            tabs = {tab_id: [] for tab_id in data.TABS}
            if self.__signed_in:
                library = self.__list_items(
                    data.create_uri("browse_main_library"), use_cache=False
                )
                tabs = data.build_tabs(library, [], self.__row_labels())
            self.__add_builtin_rows(tabs)
            self.__tabs = tabs
            if self.__closing:
                return
            if not self.__signed_in:
                self.setProperty("empty_label", self.__localize(HOME_WINDOW_SIGNED_OUT_STR_ID))
                self.__switch_tab(data.TAB_ACCOUNT, force=True)
                self.setFocusId(NAV_LIST)
                return
            self.setProperty("empty_label", self.__localize(HOME_WINDOW_EMPTY_STR_ID))
            self.__switch_tab(tab or self.__current_tab or data.TAB_HOME, force=True)
            if not tab:
                self.setFocusId(NAV_LIST)

            # the explore menu lists the categories, which takes a while, so
            # the explore tab is completed afterwards
            explore = self.__list_items(data.create_uri("browse_main_explore"))
            if self.__closing or not explore:
                return
            explore_tabs = data.build_tabs(library, explore, self.__row_labels())
            self.__add_builtin_rows(explore_tabs)
            self.__tabs[data.TAB_EXPLORE] = explore_tabs[data.TAB_EXPLORE]
            if self.__current_tab == data.TAB_EXPLORE and not self.__stack:
                self.__remember_focus()
                self.__show_current()

        self.__start(_run)

    def __wait_for_auth(self) -> bool:
        """The service authenticates on start, wait for it if Kodi just started"""
        monitor = xbmc.Monitor()
        for _ in range(AUTH_WAIT_SECONDS * 2):
            if self.__home.getProperty(utils.KODI_PROPERTY_SPOTIFY_AUTH_TOKEN):
                return True
            if self.__closing or monitor.waitForAbort(0.5):
                return False
        return False

    def __row_labels(self) -> Dict[str, str]:
        localize = self.__localize
        return {
            data.ROW_RECENT: localize(HOME_WINDOW_RECENTLY_PLAYED_STR_ID),
            data.BROWSE_PLAYLISTS: localize(HOME_WINDOW_YOUR_PLAYLISTS_STR_ID),
            data.BROWSE_SAVED_ALBUMS: localize(HOME_WINDOW_SAVED_ALBUMS_STR_ID),
            data.BROWSE_NEW_RELEASES: localize(ALL_NEW_RELEASES_STR_ID),
            data.BROWSE_CATEGORY: localize(HOME_WINDOW_GENRES_STR_ID),
            "featured": localize(FEATURED_PLAYLISTS_STR_ID),
        }

    def __add_builtin_rows(self, tabs: Dict[str, List[Dict[str, Any]]]):
        localize = self.__localize

        def _tile(label, icon, builtin, **kwargs):
            return data.make_item(
                label=label, thumb=self.__media_path(icon), builtin=builtin, **kwargs
            )

        history = self.__load_search_history()
        search_actions = [
            _tile(localize(HOME_WINDOW_NEW_SEARCH_STR_ID), "icon_music_search.png", "search")
        ]
        if history:
            search_actions.append(
                _tile(
                    localize(HOME_WINDOW_CLEAR_SEARCH_HISTORY_STR_ID),
                    "icon_clear_cache.png",
                    "clear_search_history",
                )
            )
        search_rows = [
            {
                "type": data.ROW_ACTIONS,
                "title": xbmc.getLocalizedString(KODI_SEARCH_STR_ID),
                "items": search_actions,
            }
        ]
        if self.__search_query and self.__signed_in:
            search_rows += data.search_rows(self.__search_query, self.__search_labels())
        if history:
            search_rows.append(
                {
                    "type": data.ROW_ACTIONS,
                    "title": localize(HOME_WINDOW_RECENT_SEARCHES_STR_ID),
                    "items": [
                        _tile(query, "icon_music_search.png", "search_query", query=query)
                        for query in history
                    ],
                }
            )
        tabs[data.TAB_SEARCH] = search_rows

        account = [
            _tile(
                localize(AUTHENTICATE_PLUGIN_STR_ID),
                "icon.png",
                "plugin",
                uri=data.create_uri(
                    data.AUTHENTICATE if self.__signed_in else data.CHECK_AUTHENTICATION
                ),
            ),
            _tile(
                localize(CLEAR_CACHE_STR_ID),
                "icon_clear_cache.png",
                "plugin",
                uri=data.create_uri(data.DELETE_CACHE),
            ),
            _tile(
                localize(HOME_WINDOW_SETTINGS_STR_ID),
                "icon_music_explore.png",
                f"Addon.OpenSettings({ADDON_ID})",
            ),
            _tile(localize(HOME_WINDOW_CLASSIC_STR_ID), "icon_music_library.png", "classic"),
        ]
        tabs[data.TAB_ACCOUNT] = [
            {
                "type": data.ROW_ACTIONS,
                "title": localize(HOME_WINDOW_ACCOUNT_SETTINGS_STR_ID),
                "items": account,
            }
        ]

    def __search_labels(self) -> Dict[str, str]:
        quoted = f"„{self.__search_query}“"
        return {
            data.SEARCH_TRACKS: f"{xbmc.getLocalizedString(KODI_SONGS_STR_ID)} · {quoted}",
            data.SEARCH_ARTISTS: f"{xbmc.getLocalizedString(KODI_ARTISTS_STR_ID)} · {quoted}",
            data.SEARCH_ALBUMS: f"{xbmc.getLocalizedString(KODI_ALBUMS_STR_ID)} · {quoted}",
            data.SEARCH_PLAYLISTS: f"{xbmc.getLocalizedString(KODI_PLAYLISTS_STR_ID)} · {quoted}",
        }

    def __show_current(self, refresh: bool = False):
        """Show the topmost page, or the rows of the current tab"""
        page = self.__stack[-1] if self.__stack else None
        if page and page["kind"] in ("grid", "tracks"):
            self.__show_grid(refresh)
            return
        self.__grid = None
        self.setProperty("view", "tab")
        self.setProperty("page_title", page["title"] if page else "")
        if refresh:
            self.__cache = {}
        rows = page["rows"] if page else self.__tabs.get(self.__current_tab) or []
        self.__show_rows(rows)

    def __show_rows(self, rows: List[Dict[str, Any]]):
        with self.__lock:
            self.__generation += 1
            generation = self.__generation
        self.__row_data = {}
        self.__row_offset = 0

        rows = rows[: len(ROW_LISTS)]
        for index, group_id in enumerate(ROW_GROUPS):
            self.getControl(ROW_LISTS[index]).reset()
            self.getControl(group_id).setVisible(False)

        self.setProperty("empty", "")
        if not rows:
            self.setProperty("loading", "")
            self.setProperty("empty", "true")
            return

        self.setProperty("loading", "true")

        def _row_items(row):
            if self.__stale(generation):
                return [], None
            try:
                return self.__row_items(row)
            except Exception as exc:
                log_exception(exc, "Could not list row")
                return [], None

        def _run():
            # rows are listed in parallel, but shown in order
            pool = ThreadPoolExecutor(ROW_WORKERS)
            futures = [pool.submit(_row_items, row) for row in rows]
            try:
                _show(futures)
            finally:
                # shutdown(cancel_futures=True) needs Python 3.9, Kodi on
                # Windows has 3.8
                for future in futures:
                    future.cancel()
                pool.shutdown(wait=False)

        def _show(futures):
            shown = 0
            for index, (row, future) in enumerate(zip(rows, futures)):
                if self.__stale(generation):
                    return
                items, title = future.result()
                if not items:
                    continue
                with self.__lock:
                    if self.__stale(generation):
                        return
                    self.__fill_row(index, title, items, row)
                shown += 1
                if self.__focus_from_row is not None and index >= self.__focus_from_row:
                    self.__focus_from_row = None
                    self.__set_row_offset(index)
                    self.setFocusId(ROW_LISTS[index])
                elif self.__focus_rows:
                    self.__focus_rows = False
                    self.__focus_content()
            if not self.__stale(generation):
                self.__focus_from_row = None
                self.setProperty("loading", "")
                if not shown:
                    self.setProperty("empty", "true")

        self.__start(_run)

    def __row_items(self, row: Dict[str, Any]):
        row_type = row["type"]
        if row_type == data.ROW_ACTIONS:
            return list(row["items"]), row["title"]

        if row_type == data.ROW_RECENT:
            return self.__recent_items(row), row["title"]

        all_items = self.__list_items(row["uri"])
        items = data.content_items(all_items)
        has_more = len(items) < len(all_items)
        if len(items) > data.ROW_MAX_ITEMS:
            items = items[: data.ROW_MAX_ITEMS]
            has_more = True
        if items and has_more:
            items.append(self.__show_all_item(row))
        return items, row["title"]

    def __recent_items(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not row["uri"] or not self.__setting_enabled("home_window_recent"):
            return []
        name = self.__addon.getSetting("my_recently_played_playlist_name")
        if name.upper() == "NONE":
            return []
        playlist = data.find_playlist(self.__list_items(row["uri"]), name)
        if not playlist:
            return []
        # the playlist grows with every played track, only list its end
        playlist_id = data.uri_params(playlist["uri"]).get("playlistid", "")
        uri = data.create_uri(data.BROWSE_PLAYLIST_TAIL, playlistid=playlist_id)
        return data.recent_tracks(self.__list_items(uri, use_cache=False))

    def __list_items(self, uri: str, use_cache: bool = True) -> List[Dict[str, Any]]:
        return [
            data.normalise_item(item, base_uri=uri)
            for item in self.__get_directory(uri, use_cache=use_cache)
        ]

    def __show_all_item(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return data.make_item(
            label=self.__localize(HOME_WINDOW_SHOW_ALL_STR_ID),
            title=row["title"],
            uri=row["uri"],
            subtitle=row["title"],
            is_folder=True,
            tile="all",
        )

    def __fill_row(self, index: int, title: str, items: List[Dict[str, Any]], row):
        list_id = ROW_LISTS[index]
        control = self.getControl(list_id)
        control.reset()
        control.addItems([self.__make_listitem(item) for item in items])
        self.getControl(ROW_LABELS[index]).setLabel(title or "")
        self.__row_data[list_id] = {"items": items, "row": row}
        self.getControl(ROW_GROUPS[index]).setVisible(index >= self.__row_offset)
        self.__restore_focus(list_id, control, len(items))

    # Pages: grids, track lists and row pages

    def __push_page(self, page: Dict[str, Any]):
        """Open a page, Back returns to the focused item of the current view"""
        focus_id = self.getFocusId()
        origin = None
        if focus_id in ITEM_LISTS:
            origin = (focus_id, self.getControl(focus_id).getSelectedPosition())
        if self.__stack:
            self.__stack[-1]["origin"] = origin
        else:
            self.__tab_origin = origin
        page.setdefault("origin", None)
        self.__stack.append(page)
        self.__show_current()

    def __open_grid(self, title: str, uri: str, thumb: str = "", page: int = 1, source=None):
        """
        Open a grid page. The pages of a grid show GRID_PAGE_SIZE items each,
        independent of the pages of the listing, which are loaded from the
        source of the grid as required. Listings of tracks are shown as a
        track list instead.
        """
        if source is None:
            source = self.__new_source(uri)
        self.__push_page(
            {
                "kind": "grid",
                "base_title": title,
                "title": (
                    " · ".join((title, self.__localize(HOME_WINDOW_PAGE_STR_ID) % page))
                    if page > 1
                    else title
                ),
                "thumb": thumb,
                "source": source,
                "page": page,
            }
        )

    def __open_artist(self, item: Dict[str, Any]):
        artist_id = data.uri_params(item["uri"]).get("artistid")
        if not artist_id:
            self.__open_grid(item["label"], item["uri"], item["thumb"])
            return
        self.__focus_rows = True
        self.__push_page(
            {
                "kind": "rows",
                "title": item["label"],
                "rows": data.artist_rows(artist_id, self.__artist_labels()),
            }
        )

    def __artist_labels(self) -> Dict[str, str]:
        localize = self.__localize
        return {
            data.ARTIST_TOP_TRACKS: localize(ARTIST_TOP_TRACKS_STR_ID),
            data.BROWSE_ARTIST_ALBUMS: xbmc.getLocalizedString(KODI_ALBUMS_STR_ID),
            data.BROWSE_ARTIST_SINGLES: localize(HOME_WINDOW_SINGLES_STR_ID),
            data.BROWSE_ARTIST_APPEARS_ON: localize(HOME_WINDOW_APPEARS_ON_STR_ID),
            data.RELATED_ARTISTS: localize(RELATED_ARTISTS_STR_ID),
        }

    @staticmethod
    def __new_source(uri: str) -> Dict[str, Any]:
        return {
            "uri": uri,
            "items": [],
            "next_uri": uri,
            "seen": set(),
            "lock": threading.Lock(),
        }

    def __open_next_page(self):
        """Show the next page on its own, Back returns to the previous one"""
        page = self.__stack[-1]
        self.__open_grid(
            page["base_title"], page["source"]["uri"], page["thumb"], page["page"] + 1, page["source"]
        )

    def __close_page(self):
        page = self.__stack.pop()
        if self.__stack:
            self.__restore = self.__stack[-1].get("origin")
        else:
            self.__restore = self.__tab_origin
        self.__focus_rows = not self.__restore
        self.setProperty("empty", "")
        self.__show_current()

    def __show_grid(self, refresh: bool = False):
        with self.__lock:
            self.__generation += 1
            generation = self.__generation

        page = self.__stack[-1]
        source = page["source"]
        number = page["page"]
        if refresh:
            source.update(self.__new_source(source["uri"]))

        self.setProperty("view", page["kind"])
        self.setProperty("grid_title", page["title"])
        self.setProperty("page_title", "")
        self.setProperty("cover", page["thumb"])
        self.setProperty("tracks_info", "")
        self.setProperty("empty", "")
        self.getControl(GRID_PANEL).reset()
        self.getControl(TRACK_LIST).reset()
        self.__grid = {"items": []}
        self.setProperty("loading", "true")
        localize = self.__localize

        def _run():
            start = (number - 1) * GRID_PAGE_SIZE
            end = start + GRID_PAGE_SIZE
            # load one more item than shown to know if there is a next page
            if not self.__load_source(source, end + 1, generation, refresh):
                return
            items = source["items"]
            if number == 1 and items and all(item["is_track"] for item in items):
                page["kind"] = "tracks"
                if not self.__load_source(source, TRACK_LIST_MAX_ITEMS, generation):
                    return
                with self.__lock:
                    if not self.__stale(generation):
                        self.__fill_tracks(page, source["items"])
                return

            page["kind"] = "grid"
            items = source["items"][start:end]
            if number > 1:
                items.insert(
                    0,
                    data.make_item(
                        label=localize(HOME_WINDOW_PREVIOUS_PAGE_STR_ID), is_folder=True, tile="back"
                    ),
                )
            if len(source["items"]) > end or source["next_uri"]:
                items.append(
                    data.make_item(
                        label=localize(HOME_WINDOW_NEXT_PAGE_STR_ID) % (number + 1),
                        is_folder=True,
                        tile="next",
                    )
                )
            with self.__lock:
                if self.__stale(generation):
                    return
                self.setProperty("view", "grid")
                self.__fill_grid(items)

        self.__start(_run)

    def __load_source(self, source, num_items: int, generation: int, refresh: bool = False) -> bool:
        """
        Load listing pages into the source until it has num_items items or
        the listing has no more pages. Duplicates of tracks are skipped.
        """
        with source["lock"]:
            while len(source["items"]) < num_items and source["next_uri"]:
                if self.__stale(generation):
                    return False
                next_uri = None
                for item in self.__list_items(source["next_uri"], use_cache=not refresh):
                    if item["is_next_page"]:
                        next_uri = item["uri"]
                        continue
                    track_id = item["properties"].get("spotifytrackid")
                    if track_id:
                        if track_id in source["seen"]:
                            continue
                        source["seen"].add(track_id)
                    source["items"].append(item)
                # a next page that links back to the loaded page would loop
                if next_uri == source["next_uri"]:
                    next_uri = None
                source["next_uri"] = next_uri
        return not self.__stale(generation)

    def __fill_grid(self, items: List[Dict[str, Any]]):
        panel = self.getControl(GRID_PANEL)
        self.__grid["items"].extend(items)
        panel.addItems([self.__make_listitem(item) for item in items])
        self.setProperty("loading", "")
        if not self.__grid["items"]:
            self.setProperty("empty", "true")
            return
        if self.__restore_focus(GRID_PANEL, panel, len(self.__grid["items"])):
            return
        # focus the first item rather than the previous page tile
        position = 1 if len(items) > 1 and items[0]["tile"] == "back" else 0
        panel.selectItem(position)
        self.setFocusId(GRID_PANEL)

    def __fill_tracks(self, page: Dict[str, Any], items: List[Dict[str, Any]]):
        self.setProperty("view", "tracks")
        if not page["thumb"]:
            page["thumb"] = items[0]["thumb"]
            self.setProperty("cover", page["thumb"])
        self.setProperty(
            "tracks_info",
            data.tracks_summary(items, xbmc.getLocalizedString(KODI_SONGS_STR_ID)),
        )

        buttons = self.getControl(TRACK_BUTTONS)
        buttons.reset()
        buttons.addItems(
            [
                xbmcgui.ListItem(self.__localize(HOME_WINDOW_PLAY_STR_ID), offscreen=True),
                xbmcgui.ListItem(self.__localize(HOME_WINDOW_SHUFFLE_STR_ID), offscreen=True),
            ]
        )

        track_list = self.getControl(TRACK_LIST)
        list_items = []
        for number, item in enumerate(items, 1):
            list_item = self.__make_listitem(item)
            list_item.setProperty("number", str(number))
            list_items.append(list_item)
        self.__grid["items"] = list(items)
        track_list.addItems(list_items)
        self.setProperty("loading", "")
        if self.__restore_focus(TRACK_LIST, track_list, len(items)):
            return
        self.setFocusId(TRACK_LIST)

    # Item handling

    def __make_listitem(self, item: Dict[str, Any]) -> xbmcgui.ListItem:
        list_item = xbmcgui.ListItem(
            label=item["label"], label2=item["subtitle"], path=item["uri"], offscreen=True
        )
        thumb = item["thumb"]
        list_item.setArt(
            {
                "thumb": thumb,
                "icon": thumb,
                "fanart": item["fanart"] or self.__media_path("fanart.jpg"),
            }
        )

        properties = {key: value for key, value in item["properties"].items() if value}
        properties.update(
            {
                "tile": item["tile"],
                "kind": item["kind"],
                "duration_label": item["duration_label"],
                "info_line": item["info_line"],
                "description": item.get("description", ""),
            }
        )
        for key, value in properties.items():
            list_item.setProperty(key, value)

        if item["is_track"]:
            self.__set_track_info(list_item, item)
            track_id = item["properties"].get("spotifytrackid")
            if track_id and track_id == self.__playing_track_id:
                list_item.setProperty("playing", "true")
        return list_item

    @staticmethod
    def __current_track_id() -> str:
        if not xbmc.getCondVisibility("Player.HasAudio"):
            return ""
        try:
            return data.track_id_from_uri(xbmc.Player().getPlayingFile())
        except RuntimeError:
            return ""

    def __update_playing(self):
        """Mark the playing track in all shown lists"""
        track_id = self.__current_track_id()
        if track_id == self.__playing_track_id or self.__closing:
            return
        self.__playing_track_id = track_id
        with self.__lock:
            for control_id in ITEM_LISTS:
                control = self.getControl(control_id)
                for position in range(control.size()):
                    list_item = control.getListItem(position)
                    playing = track_id and list_item.getProperty("spotifytrackid") == track_id
                    list_item.setProperty("playing", "true" if playing else "")

    @staticmethod
    def __set_track_info(list_item: xbmcgui.ListItem, item: Dict[str, Any]):
        list_item.setProperty("do_not_analyze", "true")
        list_item.setMimeType("audio/wave")
        list_item.setContentLookup(False)
        info = {
            "title": item["title"],
            "artist": item["artist"],
            "album": item["album"],
            "genre": item["genre"],
            "year": int(item["year"] or 0),
            "tracknumber": int(item["track_number"] or 0),
            "duration": int(item["duration"] or 0),
        }
        try:
            tag = list_item.getMusicInfoTag()
            tag.setTitle(info["title"])
            tag.setArtist(info["artist"])
            tag.setAlbum(info["album"])
            tag.setGenres([info["genre"]] if info["genre"] else [])
            tag.setYear(info["year"])
            tag.setTrack(info["tracknumber"])
            tag.setDuration(info["duration"])
        except AttributeError:
            # Kodi 19 has no setters on the info tag
            list_item.setInfo("music", info)

    def __focused_item(self, control_id: int) -> Optional[Dict[str, Any]]:
        if control_id in (GRID_PANEL, TRACK_LIST):
            items = self.__grid["items"] if self.__grid else []
        elif control_id in ROW_LISTS:
            row = self.__row_data.get(control_id)
            items = row["items"] if row else []
        else:
            return None
        position = self.getControl(control_id).getSelectedPosition()
        if 0 <= position < len(items):
            return items[position]
        return None

    def __container_items(self, control_id: int) -> List[Dict[str, Any]]:
        if control_id == TRACK_LIST and self.__grid:
            return self.__grid["items"]
        if control_id == GRID_PANEL and self.__stack:
            # play the whole listing, not just the shown page
            return self.__stack[-1]["source"]["items"]
        row = self.__row_data.get(control_id)
        return row["items"] if row else []

    def __activate(self, control_id: int, item: Dict[str, Any]):
        builtin = item.get("builtin")
        if builtin:
            self.__run_builtin(builtin, item)
            return

        tile = item["tile"]
        if tile == "back":
            self.__close_page()
            return
        if tile == "all":
            self.__open_grid(item["title"], item["uri"])
            return
        if tile == "next":
            self.__open_next_page()
            return

        if item["is_track"]:
            tracks = [entry for entry in self.__container_items(control_id) if entry["is_track"]]
            start = next((index for index, entry in enumerate(tracks) if entry is item), 0)
            self.__play_tracks(tracks, start)
            return

        if item["kind"] == "artist":
            self.__open_artist(item)
            return

        if item["is_folder"]:
            self.__open_grid(item["label"], item["uri"], item["thumb"] if tile != "icon" else "")
            return

        self.__execute(f"RunPlugin({item['uri']})", refresh_view=True)

    def __run_builtin(self, builtin: str, item: Dict[str, Any]):
        if builtin == "classic":
            self.__execute(f"ActivateWindow(Music,{data.root_uri()},return)")
        elif builtin == "search":
            self.__search()
        elif builtin == "search_query":
            self.__run_search(item["query"])
        elif builtin == "clear_search_history":
            self.__save_search_history([])
            self.__search_query = ""
            self.__reload_search_tab()
        elif builtin == "plugin":
            # sign in and clearing the cache change the content of all tabs
            self.__execute(f"RunPlugin({item['uri']})", refresh_root=True)
        else:
            self.__execute(builtin, refresh_root=True)

    # Playback

    def __play_tracks(self, items: List[Dict[str, Any]], start: int = 0, shuffle: bool = False):
        tracks = [item for item in items if item["is_track"]]
        if not tracks:
            return
        if shuffle:
            tracks = list(tracks)
            random.shuffle(tracks)
            start = 0
        self.__remember_focus()
        playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
        playlist.clear()
        for track in tracks:
            playlist.add(track["uri"], self.__make_listitem(track))
        xbmc.Player().play(playlist, startpos=min(start, len(tracks) - 1))

    def __queue_tracks(self, items: List[Dict[str, Any]]):
        tracks = [item for item in items if item["is_track"]]
        if not tracks:
            return
        playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
        playing = xbmc.getCondVisibility("Player.HasAudio")
        if not playing:
            playlist.clear()
        for track in tracks:
            playlist.add(track["uri"], self.__make_listitem(track))
        if not playing:
            xbmc.Player().play(playlist)

    def __play_folder(self, item: Dict[str, Any], shuffle: bool = False, queue: bool = False):
        """Play the tracks of an album, playlist or the top tracks of an artist"""
        uri = item["uri"]
        if item["kind"] == "artist":
            artist_id = data.uri_params(uri).get("artistid", "")
            uri = data.create_uri(data.ARTIST_TOP_TRACKS, artistid=artist_id)
        self.setProperty("loading", "true")

        def _run():
            try:
                tracks = [entry for entry in self.__list_items(uri) if entry["is_track"]]
            finally:
                self.setProperty("loading", "")
            if self.__closing:
                return
            if queue:
                self.__queue_tracks(tracks)
            else:
                self.__play_tracks(tracks, shuffle=shuffle)

        self.__start(_run)

    # Search

    def __search(self):
        query = xbmcgui.Dialog().input(xbmc.getLocalizedString(KODI_ENTER_SEARCH_STRING_STR_ID))
        if query:
            self.__run_search(query.strip())

    def __run_search(self, query: str):
        if not query:
            return
        self.__search_query = query
        self.__save_search_history(data.add_search_history(self.__load_search_history(), query))
        # focus the first row of results, the first row has the search tiles
        self.__focus_from_row = 1
        self.__reload_search_tab()

    def __reload_search_tab(self):
        self.__add_builtin_rows(self.__tabs)
        if self.__current_tab == data.TAB_SEARCH and not self.__stack:
            self.__show_current()

    @staticmethod
    def __load_search_history() -> List[str]:
        try:
            with open(SEARCH_HISTORY_FILE, encoding="utf-8") as history_file:
                history = json.load(history_file)
            return [entry for entry in history if isinstance(entry, str) and entry]
        except (OSError, ValueError):
            return []

    @staticmethod
    def __save_search_history(history: List[str]):
        try:
            os.makedirs(os.path.dirname(SEARCH_HISTORY_FILE), exist_ok=True)
            with open(SEARCH_HISTORY_FILE, "w", encoding="utf-8") as history_file:
                json.dump(history, history_file)
        except OSError as exc:
            log_exception(exc, "Could not save the search history")

    # Context menu

    def __show_context_menu(self, control_id: int, item: Dict[str, Any]):
        if item.get("builtin") or item["tile"] in {"all", "back", "next"}:
            return
        localize = self.__localize

        entries = []
        if item["is_track"]:
            entries.append((localize(HOME_WINDOW_PLAY_STR_ID), "play"))
            entries.append((localize(HOME_WINDOW_QUEUE_STR_ID), "queue"))
        elif item["kind"] in ("album", "playlist", "artist"):
            entries.append((localize(HOME_WINDOW_PLAY_STR_ID), "play_folder"))
            entries.append((localize(HOME_WINDOW_SHUFFLE_STR_ID), "shuffle_folder"))
            entries.append((localize(HOME_WINDOW_QUEUE_STR_ID), "queue_folder"))
            entries.append((localize(HOME_WINDOW_OPEN_STR_ID), "open"))
        elif item["is_folder"]:
            entries.append((localize(HOME_WINDOW_OPEN_STR_ID), "open"))
        entries += data.context_menu_entries(item)
        if not entries:
            return

        choice = xbmcgui.Dialog().contextmenu([label for label, _ in entries])
        if choice < 0:
            return
        label, action = entries[choice]

        if action in ("play", "open"):
            self.__activate(control_id, item)
        elif action == "queue":
            self.__queue_tracks([item])
        elif action in ("play_folder", "shuffle_folder", "queue_folder"):
            self.__play_folder(
                item, shuffle=action == "shuffle_folder", queue=action == "queue_folder"
            )
        elif data.plugin_action(action) == data.REFRESH_LISTING:
            self.__execute(action, refresh_view=True)
        else:
            target = data.navigation_target(action)
            if not target:
                self.__execute(action, refresh_view=True)
            elif data.uri_action(target) == data.BROWSE_ARTIST:
                self.__open_artist(dict(item, label=item["artist"] or label, uri=target))
            else:
                self.__open_grid(label, target)

    # Helpers

    def __execute(self, command: str, refresh_view: bool = False, refresh_root: bool = False):
        log_msg(f"Executing: {command}")
        xbmc.executebuiltin(command)
        if refresh_root or refresh_view:
            self.__start(self.__wait_and_refresh, refresh_root)

    def __wait_and_refresh(self, refresh_root: bool):
        """Wait until the triggered action has finished, then refresh"""
        monitor = xbmc.Monitor()
        if monitor.waitForAbort(1):
            return
        # sign in waits for the user to connect from the Spotify app
        for _ in range(600):
            if self.__closing:
                return
            if not xbmc.getCondVisibility(
                "System.HasActiveModalDialog"
                " | Window.IsActive(busydialog)"
                " | Window.IsActive(busydialognocancel)"
            ):
                break
            if monitor.waitForAbort(0.5):
                return
        self.__cache = {}
        if refresh_root:
            self.__remember_focus()
            self.__load_root(tab=self.__current_tab)
        else:
            self.__refresh_view()

    def __remember_focus(self):
        focus_id = self.getFocusId()
        if focus_id in ITEM_LISTS:
            self.__restore = (focus_id, self.getControl(focus_id).getSelectedPosition())
        else:
            self.__restore = None

    def __refresh_view(self):
        # the focus is remembered before playback, as Kodi may move it while
        # the window is activated again
        if not self.__restore:
            self.__remember_focus()
        self.__cache = {}
        if self.__current_tab:
            self.__show_current(refresh=True)

    def __restore_focus(self, control_id: int, control, num_items: int) -> bool:
        restore = self.__restore
        if not restore or restore[0] != control_id:
            return False
        self.__restore = None
        position = min(restore[1], num_items - 1)
        if position >= 0:
            control.selectItem(position)
        if control_id in ROW_LISTS:
            self.__set_row_offset(ROW_LISTS.index(control_id))
        self.setFocusId(control_id)
        return True

    def __localize(self, string_id: int) -> str:
        return self.__addon.getLocalizedString(string_id)

    def __setting_enabled(self, setting_id: str) -> bool:
        return self.__addon.getSetting(setting_id) != "false"

    def __media_path(self, filename: str) -> str:
        return os.path.join(self.__addon_path, "resources", filename)

    def __update_user(self):
        user = ""
        if self.__signed_in:
            try:
                import spotipy

                me = spotipy.Spotify(auth=utils.get_cached_auth_token()).me()
                user = me.get("display_name") or me.get("id") or ""
            except Exception as exc:
                log_exception(exc, "Could not get the Spotify user")
        self.setProperty("user", user)

    def __get_directory(self, uri: str, use_cache: bool = True) -> List[Dict[str, Any]]:
        with self.__lock:
            listing_lock = self.__listing_locks.setdefault(uri, threading.Lock())
        with listing_lock:
            return self.__get_directory_locked(uri, use_cache)

    def __get_directory_locked(self, uri: str, use_cache: bool) -> List[Dict[str, Any]]:
        now = time.time()
        if use_cache:
            cached = self.__cache.get(uri)
            if cached and now - cached[0] < CACHE_SECONDS:
                return cached[1]
        response = json.loads(xbmc.executeJSONRPC(json.dumps(data.build_request(uri))))
        if "error" in response:
            # older Kodi versions do not support all properties, e.g. the custom
            # properties which contain the context menu
            log_msg(f"Listing failed, retrying with basic properties: {response['error']}")
            properties = [name for name in data.LIST_PROPERTIES if name != "customproperties"]
            response = xbmc.executeJSONRPC(json.dumps(data.build_request(uri, properties)))
        items = data.parse_response(response)
        self.__cache[uri] = (now, items)
        return items

    def __stale(self, generation: int) -> bool:
        return self.__closing or generation != self.__generation

    def __start(self, target, *args):
        def _run():
            try:
                target(*args)
            except Exception as exc:
                log_exception(exc, "Home window task failed")
                self.setProperty("loading", "")

        thread = threading.Thread(target=_run)
        thread.daemon = True
        thread.start()
        return thread


def _activate_running_window(value: str, timeout: int = 3) -> bool:
    """
    Bring an already running home window to the front.

    :return: True if the home window is running, False if it is not, e.g. if
             it was not closed cleanly
    """
    if value.startswith("starting:"):
        # still starting, ignore unless it never finished starting
        try:
            return time.time() - float(value.partition(":")[2]) < 30
        except ValueError:
            return False
    if not value.isdigit():
        return False
    xbmc.executebuiltin(f"ActivateWindow({value})")
    monitor = xbmc.Monitor()
    for _ in range(timeout * 10):
        if xbmc.getCondVisibility(f"Window.IsActive({value})"):
            return True
        if monitor.waitForAbort(0.1):
            break
    return False


def open_home_window() -> None:
    home = xbmcgui.Window(utils.ADDON_WINDOW_ID)
    running = home.getProperty(HOME_WINDOW_ACTIVE)
    if running and _activate_running_window(running):
        return
    home.setProperty(HOME_WINDOW_ACTIVE, f"starting:{time.time()}")
    try:
        addon_path = xbmcaddon.Addon(id=ADDON_ID).getAddonInfo("path")
        window = HomeWindow("script-spotify-home.xml", addon_path, "default", "1080i")
        window.doModal()
        del window
    finally:
        home.clearProperty(HOME_WINDOW_ACTIVE)
