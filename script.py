#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
    plugin.audio.spotify
    Script entry point, opens the TV interface: RunScript(plugin.audio.spotify,home)
    RunScript(plugin.audio.spotify,restart_track) is used by the service, see
    resources/lib/video_player_guard.py
"""

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restart_track":
        from resources.lib.video_player_guard import restart_track

        restart_track()
    else:
        from resources.lib.home_window import open_home_window

        open_home_window()
