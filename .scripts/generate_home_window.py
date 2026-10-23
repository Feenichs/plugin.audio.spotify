#!/usr/bin/env python3
"""
Generates the skin files of the TV interface (home window):

- resources/skins/default/1080i/script-spotify-home.xml
- resources/skins/default/media/sp_*.png

Kodi windows cannot define their own includes, and the eight content rows of
the home window use identical tile layouts, so the window XML is generated
from the templates below. Re-run this script after changing them:

    python3 .scripts/generate_home_window.py
"""

import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIN = os.path.join(ROOT, "resources", "skins", "default")
XML_PATH = os.path.join(SKIN, "1080i", "script-spotify-home.xml")
MEDIA = os.path.join(SKIN, "media")

ADDON = "plugin.audio.spotify"
NUM_ROWS = 8

# Colours (AARRGGBB)
BACKGROUND = "FF121212"
TEXT = "FFFFFFFF"
TEXT_DIM = "FFB3B3B3"
TEXT_DARK = "FF121212"
ACCENT = "FF1ED760"
TILE = "FF242424"
LYRICS_DIM = "70FFFFFF"
HIGHLIGHT = "FF2E2E2E"

NAV_WIDTH = 235

# Tile geometry, thumbnails are square (covers) or round (artists)
TILE_WIDTH = 280
TILE_HEIGHT = 350
THUMB_LEFT = 12
THUMB_TOP = 10
THUMB_SIZE = 256
GRID_WIDTH = 300

# Item is the currently playing track, the property is set by the window
PLAYING = "String.IsEqual(ListItem.Property(playing),true)"


# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------


def write_png(path, width, height, pixel):
    """Write an RGBA PNG, pixel(x, y) returns (r, g, b, a)"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(pixel(x, y))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
            chunk(b"IEND", b""),
        )
    )
    with open(path, "wb") as png_file:
        png_file.write(png)


def rounded_coverage(x, y, width, height, radius, samples=4):
    """Anti-aliased coverage (0..1) of a pixel within a rounded rectangle"""
    inside = 0
    for sub_y in range(samples):
        for sub_x in range(samples):
            px = x + (sub_x + 0.5) / samples
            py = y + (sub_y + 0.5) / samples
            cx = min(max(px, radius), width - radius)
            cy = min(max(py, radius), height - radius)
            if (px - cx) ** 2 + (py - cy) ** 2 <= radius**2:
                inside += 1
    return inside / (samples * samples)


def rounded(width, height, radius, outline=0):
    def pixel(x, y):
        alpha = rounded_coverage(x, y, width, height, radius)
        if outline:
            inner = rounded_coverage(
                x - outline,
                y - outline,
                width - 2 * outline,
                height - 2 * outline,
                max(radius - outline, 1),
            )
            if outline <= x < width - outline and outline <= y < height - outline:
                alpha = max(0.0, alpha - inner)
        return 255, 255, 255, int(round(255 * alpha))

    return pixel


def generate_textures():
    if not os.path.isdir(MEDIA):
        os.makedirs(MEDIA)

    write_png(os.path.join(MEDIA, "sp_white.png"), 8, 8, lambda x, y: (255, 255, 255, 255))
    # 9-slice textures, used with border="12" / border="31" / border="20" / border="4"
    write_png(os.path.join(MEDIA, "sp_rounded.png"), 64, 64, rounded(64, 64, 10))
    write_png(os.path.join(MEDIA, "sp_pill.png"), 64, 64, rounded(64, 64, 31))
    write_png(os.path.join(MEDIA, "sp_focus.png"), 64, 64, rounded(64, 64, 14, outline=5))
    write_png(os.path.join(MEDIA, "sp_scroll.png"), 8, 32, rounded(8, 32, 4))
    write_png(os.path.join(MEDIA, "sp_focus_round.png"), 128, 128, rounded(128, 128, 64, outline=5))
    # alpha masks for covers and artist images
    write_png(os.path.join(MEDIA, "sp_mask.png"), THUMB_SIZE, THUMB_SIZE, rounded(256, 256, 10))
    write_png(os.path.join(MEDIA, "sp_mask_round.png"), THUMB_SIZE, THUMB_SIZE, rounded(256, 256, 128))
    # gradient used to blend the preview image into the background
    write_png(
        os.path.join(MEDIA, "sp_gradient_bottom.png"),
        4,
        256,
        lambda x, y: (18, 18, 18, int(255 * (y / 255.0) ** 1.2)),
    )


# ---------------------------------------------------------------------------
# Window XML
# ---------------------------------------------------------------------------


def localize(string_id):
    return "$ADDON[%s %d]" % (ADDON, string_id)


def is_tile(kind):
    return "String.IsEqual(ListItem.Property(tile),%s)" % kind


def tile_layout(list_id, focused, width=TILE_WIDTH):
    """Layout of one tile; list_id is used for focus dependent styling"""
    offset = (width - TILE_WIDTH) // 2
    has_focus = "Control.HasFocus(%d)" % list_id
    zoom = ""
    focus_ring = ""
    if focused:
        zoom = (
            '<animation effect="zoom" end="105" center="{c},{c2}" time="150"'
            ' tween="sine" condition="{focus}">Conditional</animation>'
        ).format(
            c=offset + THUMB_LEFT + THUMB_SIZE // 2,
            c2=THUMB_TOP + THUMB_SIZE // 2,
            focus=has_focus,
        )
        focus_ring = """
            <control type="image">
                <left>{left}</left><top>{top}</top>
                <width>{size}</width><height>{size}</height>
                <texture border="20" colordiffuse="FFFFFFFF">sp_focus.png</texture>
                <visible>{focus} + !{round}</visible>
            </control>
            <control type="image">
                <left>{left}</left><top>{top}</top>
                <width>{size}</width><height>{size}</height>
                <texture colordiffuse="FFFFFFFF">sp_focus_round.png</texture>
                <visible>{focus} + {round}</visible>
            </control>""".format(
            left=THUMB_LEFT - 7,
            top=THUMB_TOP - 7,
            size=THUMB_SIZE + 14,
            focus=has_focus,
            round=is_tile("round"),
        )

    label_tiles = " | ".join(is_tile(kind) for kind in ("all", "back", "next"))
    return """
        <control type="group">
            <left>{offset}</left>
            {zoom}
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <texture border="12" colordiffuse="{tile}">sp_rounded.png</texture>
                <visible>!{round}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <texture colordiffuse="{tile}">sp_mask_round.png</texture>
                <visible>{round}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="sp_mask.png">$INFO[ListItem.Art(thumb)]</texture>
                <visible>{square}</visible>
            </control>
            <control type="image">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="sp_mask_round.png">$INFO[ListItem.Art(thumb)]</texture>
                <visible>{round}</visible>
            </control>
            <control type="image">
                <left>{il}</left><top>{it}</top><width>{isz}</width><height>{isz}</height>
                <aspectratio>keep</aspectratio>
                <texture background="true">$INFO[ListItem.Art(thumb)]</texture>
                <visible>{icon}</visible>
            </control>
            <control type="label">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <align>center</align><aligny>center</aligny>
                <font>font30_title</font>
                <textcolor>{text}</textcolor>
                <label>{show_all}  &#x203A;</label>
                <visible>{all}</visible>
            </control>
            <control type="label">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>&#x2039;  $INFO[ListItem.Label]</label>
                <visible>{back}</visible>
            </control>
            <control type="label">
                <left>{tl}</left><top>{tt}</top><width>{ts}</width><height>{ts}</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]  &#x203A;</label>
                <visible>{next}</visible>
            </control>
            <control type="group">
                <left>{bl}</left><top>{bt}</top>
                <visible>{playing}</visible>
                <control type="image">
                    <width>56</width><height>56</height>
                    <texture colordiffuse="{accent}">sp_mask_round.png</texture>
                </control>
                <control type="label">
                    <width>56</width><height>56</height>
                    <align>center</align><aligny>center</aligny>
                    <font>font14</font><textcolor>{dark}</textcolor>
                    <label>&#x266B;</label>
                </control>
            </control>
            {focus_ring}
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>36</height>
                <font>font13</font>
                <textcolor>{text}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{playing} + ![{label_tiles}]</visible>
            </control>
            <control type="label">
                <left>{xl}</left><top>{xt}</top><width>{xw}</width><height>36</height>
                <font>font13</font>
                <textcolor>{accent}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>{playing}</visible>
            </control>
            <control type="label">
                <left>{xl}</left><top>{st}</top><width>{xw}</width><height>30</height>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label2]</label>
            </control>
        </control>""".format(
        offset=offset,
        zoom=zoom,
        focus_ring=focus_ring,
        tl=THUMB_LEFT,
        tt=THUMB_TOP,
        ts=THUMB_SIZE,
        il=THUMB_LEFT + 48,
        it=THUMB_TOP + 48,
        isz=THUMB_SIZE - 96,
        bl=THUMB_LEFT + THUMB_SIZE - 68,
        bt=THUMB_TOP + THUMB_SIZE - 68,
        xl=THUMB_LEFT + 2,
        xt=THUMB_TOP + THUMB_SIZE + 10,
        xw=THUMB_SIZE - 4,
        st=THUMB_TOP + THUMB_SIZE + 46,
        tile=TILE,
        text=TEXT,
        dim=TEXT_DIM,
        dark=TEXT_DARK,
        accent=ACCENT,
        square=is_tile("square"),
        round=is_tile("round"),
        icon=is_tile("icon"),
        all=is_tile("all"),
        back=is_tile("back"),
        next=is_tile("next"),
        label_tiles=label_tiles,
        playing=PLAYING,
        show_all=localize(11109),
        scroll="true" if focused else "false",
    )


def row(index):
    list_id = 301 + index
    return """
            <control type="group" id="{group_id}">
                <width>1920</width><height>400</height>
                <control type="label" id="{label_id}">
                    <left>80</left><top>0</top><width>1600</width><height>50</height>
                    <font>font30_title</font>
                    <textcolor>{text}</textcolor>
                </control>
                <control type="list" id="{list_id}">
                    <left>60</left><top>50</top><width>1860</width><height>350</height>
                    <orientation>horizontal</orientation>
                    <onup>{onup}</onup>
                    <ondown>{ondown}</ondown>
                    <scrolltime tween="sine">200</scrolltime>
                    <itemlayout width="{tile_width}" height="{tile_height}">{item}
                    </itemlayout>
                    <focusedlayout width="{tile_width}" height="{tile_height}">{focused}
                    </focusedlayout>
                </control>
            </control>""".format(
        group_id=501 + index,
        label_id=401 + index,
        list_id=list_id,
        # moving between rows is handled by the window, which hides the rows
        # above the focused one, so that the focused row is always on top
        onup=list_id,
        ondown=list_id,
        text=TEXT,
        tile_width=TILE_WIDTH,
        tile_height=TILE_HEIGHT,
        item=tile_layout(list_id, False),
        focused=tile_layout(list_id, True),
    )


def nav_layout(focused):
    has_focus = "Control.HasFocus(100)"
    active = "String.IsEqual(ListItem.Property(active),true)"
    pill = ""
    dark_label = ""
    if focused:
        pill = """
            <control type="image">
                <left>6</left><top>6</top><width>{width}</width><height>58</height>
                <texture border="28" colordiffuse="FFFFFFFF">sp_pill.png</texture>
                <visible>{focus}</visible>
            </control>""".format(
            focus=has_focus, width=NAV_WIDTH - 12
        )
        dark_label = """
            <control type="label">
                <width>{nav_width}</width><height>70</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{dark}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{focus}</visible>
            </control>""".format(
            dark=TEXT_DARK, focus=has_focus, nav_width=NAV_WIDTH
        )
    focus_condition = (" + !%s" % has_focus) if focused else ""
    return """{pill}
            <control type="label">
                <width>{nav_width}</width><height>70</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{active}{focus_condition}</visible>
            </control>
            <control type="label">
                <width>{nav_width}</width><height>70</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{active}{focus_condition}</visible>
            </control>{dark_label}
            <control type="image">
                <left>{bar_left}</left><top>62</top><width>60</width><height>4</height>
                <texture colordiffuse="{accent}">sp_white.png</texture>
                <visible>{active}{focus_condition}</visible>
            </control>""".format(
        pill=pill,
        dark_label=dark_label,
        text=TEXT,
        dim=TEXT_DIM,
        accent=ACCENT,
        active=active,
        focus_condition=focus_condition,
        nav_width=NAV_WIDTH,
        bar_left=(NAV_WIDTH - 60) // 2,
    )


def track_layout(focused):
    """Layout of one entry of the track list of albums and playlists"""
    has_focus = "Control.HasFocus(700)"
    highlight = ""
    if focused:
        highlight = """
            <control type="image">
                <width>1240</width><height>84</height>
                <texture border="12" colordiffuse="{highlight}">sp_rounded.png</texture>
                <visible>{focus}</visible>
            </control>
            <control type="image">
                <left>0</left><top>18</top><width>5</width><height>48</height>
                <texture colordiffuse="{accent}">sp_white.png</texture>
                <visible>{focus}</visible>
            </control>""".format(
            highlight=HIGHLIGHT, accent=ACCENT, focus=has_focus
        )
    return """{highlight}
            <control type="label">
                <left>10</left><width>60</width><height>84</height>
                <align>center</align><aligny>center</aligny>
                <font>font12</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Property(number)]</label>
                <visible>!{playing}</visible>
            </control>
            <control type="label">
                <left>10</left><width>60</width><height>84</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{accent}</textcolor>
                <label>&#x266B;</label>
                <visible>{playing}</visible>
            </control>
            <control type="image">
                <left>84</left><top>10</top><width>64</width><height>64</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="sp_mask.png">$INFO[ListItem.Art(thumb)]</texture>
            </control>
            <control type="label">
                <left>170</left><top>8</top><width>880</width><height>38</height>
                <font>font13</font><textcolor>{text}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{playing}</visible>
            </control>
            <control type="label">
                <left>170</left><top>8</top><width>880</width><height>38</height>
                <font>font13</font><textcolor>{accent}</textcolor>
                <scroll>{scroll}</scroll>
                <label>$INFO[ListItem.Label]</label>
                <visible>{playing}</visible>
            </control>
            <control type="label">
                <left>170</left><top>44</top><width>880</width><height>32</height>
                <font>font10</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Label2]</label>
            </control>
            <control type="label">
                <left>1060</left><width>150</width><height>84</height>
                <align>right</align><aligny>center</aligny>
                <font>font12</font><textcolor>{dim}</textcolor>
                <label>$INFO[ListItem.Property(duration_label)]</label>
            </control>""".format(
        highlight=highlight,
        text=TEXT,
        dim=TEXT_DIM,
        accent=ACCENT,
        playing=PLAYING,
        scroll="true" if focused else "false",
    )


def button_layout(focused):
    """Layout of the play / shuffle buttons of the track list"""
    has_focus = "Control.HasFocus(710)"
    pill = ""
    dark_label = ""
    focus_condition = ""
    if focused:
        pill = """
            <control type="image">
                <width>440</width><height>68</height>
                <texture border="31" colordiffuse="{accent}">sp_pill.png</texture>
                <visible>{focus}</visible>
            </control>""".format(
            accent=ACCENT, focus=has_focus
        )
        dark_label = """
            <control type="label">
                <width>440</width><height>68</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{dark}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                <visible>{focus}</visible>
            </control>""".format(
            dark=TEXT_DARK, focus=has_focus
        )
        focus_condition = "<visible>!%s</visible>" % has_focus
    return """
            <control type="image">
                <width>440</width><height>68</height>
                <texture border="31" colordiffuse="{tile}">sp_pill.png</texture>
                {focus_condition}
            </control>{pill}
            <control type="label">
                <width>440</width><height>68</height>
                <align>center</align><aligny>center</aligny>
                <font>font14</font><textcolor>{text}</textcolor>
                <label>$INFO[ListItem.Label]</label>
                {focus_condition}
            </control>{dark_label}""".format(
        pill=pill,
        dark_label=dark_label,
        focus_condition=focus_condition,
        tile=HIGHLIGHT,
        text=TEXT,
    )


def scrollbar(control_id, list_id, handle_id, left, top, height):
    """
    A slider beside a long list, like in the Kodi menus. It is only a short
    green line without a track, its length shows the visible part. Right
    moves to an invisible button beside it, Up and Down on that button move
    the list continuously (see HomeWindow.__scroll), not every remote has
    page keys. The scrollbar itself only pages, it is left to the mouse.
    """
    return """
            <control type="button" id="{handle_id}">
                <left>{handle_left}</left><top>{top}</top><width>48</width><height>{height}</height>
                <texturefocus>-</texturefocus>
                <texturenofocus>-</texturenofocus>
                <onleft>{list_id}</onleft>
                <visible>Container({list_id}).HasNext | Container({list_id}).HasPrevious</visible>
            </control>
            <control type="scrollbar" id="{control_id}">
                <left>{left}</left><top>{top}</top><width>8</width><height>{height}</height>
                <texturesliderbackground colordiffuse="00FFFFFF">sp_white.png</texturesliderbackground>
                <texturesliderbar border="4" colordiffuse="{accent}">sp_scroll.png</texturesliderbar>
                <texturesliderbarfocus border="4" colordiffuse="{accent}">sp_scroll.png</texturesliderbarfocus>
                <textureslidernib>-</textureslidernib>
                <textureslidernibfocus>-</textureslidernibfocus>
                <orientation>vertical</orientation>
                <showonepage>false</showonepage>
                <onleft>{list_id}</onleft>
                <animation effect="fade" start="100" end="50" time="150" condition="!Control.HasFocus({handle_id})">Conditional</animation>
            </control>""".format(
        control_id=control_id,
        list_id=list_id,
        handle_id=handle_id,
        handle_left=left - 20,
        left=left,
        top=top,
        height=height,
        accent=ACCENT,
    )


def lyrics_layout(focused):
    """
    One line of the lyrics. The current line of synced lyrics is the
    selected one, it is shown larger and white.
    """
    synced = "String.IsEqual(Window.Property(lyrics_synced),true)"
    if focused:
        return """
            <control type="label">
                <width>1240</width><height>120</height>
                <aligny>center</aligny>
                <font>font36_title</font><textcolor>{text}</textcolor>
                <wrapmultiline>true</wrapmultiline>
                <label>$INFO[ListItem.Label]</label>
            </control>""".format(
            text=TEXT
        )
    return """
            <control type="label">
                <width>1240</width><height>120</height>
                <aligny>center</aligny>
                <font>font30_title</font><textcolor>{dim}</textcolor>
                <wrapmultiline>true</wrapmultiline>
                <label>$INFO[ListItem.Label]</label>
                <visible>{synced}</visible>
            </control>
            <control type="label">
                <width>1240</width><height>120</height>
                <aligny>center</aligny>
                <font>font30_title</font><textcolor>{text}</textcolor>
                <wrapmultiline>true</wrapmultiline>
                <label>$INFO[ListItem.Label]</label>
                <visible>!{synced}</visible>
            </control>""".format(
        dim=LYRICS_DIM, text=TEXT, synced=synced
    )


WINDOW_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Generated by .scripts/generate_home_window.py - do not edit manually -->
<window>
    <defaultcontrol>100</defaultcontrol>
    <backgroundcolor>{background}</backgroundcolor>
    <animation effect="fade" time="250">WindowOpen</animation>
    <animation effect="fade" time="200">WindowClose</animation>
    <controls>
        <!--
            A video that keeps playing in the background has to be rendered,
            otherwise it runs out of sync with the audio. It is shown as a
            small preview on the start page, or dimmed behind the other views.
        -->
        <control type="videowindow">
            <width>1920</width><height>1080</height>
            <visible>Player.HasVideo + !{tab_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="{background}">sp_white.png</texture>
            <visible>!Player.HasVideo | {tab_view}</visible>
        </control>
        <control type="image">
            <width>1920</width><height>1080</height>
            <texture colordiffuse="{background_dimmed}">sp_white.png</texture>
            <visible>Player.HasVideo + !{tab_view}</visible>
        </control>

        <!-- Preview of the selected item -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),tab)</visible>
            <control type="group">
                <visible>String.IsEqual(Window.Property(hero),true)</visible>
                <!-- the cover, faded into the background, tints the preview -->
                <control type="image">
                    <width>1920</width><height>600</height>
                    <aspectratio>scale</aspectratio>
                    <fadetime>300</fadetime>
                    <colordiffuse>40FFFFFF</colordiffuse>
                    <texture background="true">$INFO[ListItem.Art(fanart)]</texture>
                </control>
                <control type="image">
                    <top>250</top><width>1920</width><height>350</height>
                    <texture>sp_gradient_bottom.png</texture>
                </control>
                <control type="image">
                    <width>1920</width><height>220</height>
                    <texture flipy="true">sp_gradient_bottom.png</texture>
                </control>
                <control type="image">
                    <left>1420</left><top>130</top><width>420</width><height>420</height>
                    <aspectratio>scale</aspectratio>
                    <fadetime>300</fadetime>
                    <texture background="true" diffuse="sp_mask.png">$INFO[ListItem.Art(thumb)]</texture>
                    <visible>String.IsEqual(ListItem.Property(tile),square) + !Player.HasVideo</visible>
                </control>
                <control type="image">
                    <left>1420</left><top>130</top><width>420</width><height>420</height>
                    <aspectratio>scale</aspectratio>
                    <fadetime>300</fadetime>
                    <texture background="true" diffuse="sp_mask_round.png">$INFO[ListItem.Art(thumb)]</texture>
                    <visible>String.IsEqual(ListItem.Property(tile),round) + !Player.HasVideo</visible>
                </control>
            </control>
            <!-- mini preview of a video playing in the background -->
            <control type="group">
                <left>1360</left><top>150</top>
                <visible>Player.HasVideo</visible>
                <control type="image">
                    <left>-4</left><top>-4</top><width>488</width><height>278</height>
                    <texture border="16" colordiffuse="40FFFFFF">sp_rounded.png</texture>
                </control>
                <control type="videowindow">
                    <width>480</width><height>270</height>
                </control>
                <control type="label">
                    <top>284</top><width>480</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{text}</textcolor>
                    <scroll>true</scroll>
                    <label>$INFO[VideoPlayer.Title]</label>
                </control>
                <control type="label">
                    <top>320</top><width>480</width><height>32</height>
                    <font>font10</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[Player.Time]$INFO[Player.Duration, / ]</label>
                </control>
            </control>
            <control type="group">
                <left>80</left><top>130</top>
                <control type="label">
                    <width>1250</width><height>36</height>
                    <font>font12</font>
                    <textcolor>{accent}</textcolor>
                    <label>$INFO[Window.Property(page_title)]</label>
                </control>
                <control type="label">
                    <top>40</top><width>1250</width><height>80</height>
                    <font>font45_title</font>
                    <textcolor>{text}</textcolor>
                    <label>$INFO[ListItem.Label]</label>
                </control>
                <control type="label">
                    <top>122</top><width>1250</width><height>44</height>
                    <font>font14</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[ListItem.Label2]</label>
                </control>
                <control type="label">
                    <top>176</top><width>1250</width><height>40</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[ListItem.Property(info_line)]</label>
                </control>
                <control type="textbox">
                    <top>230</top><width>1150</width><height>180</height>
                    <font>font12</font>
                    <textcolor>{dim}</textcolor>
                    <label>$INFO[ListItem.Property(description)]</label>
                </control>
            </control>
        </control>

        <!-- Header -->
        <control type="image">
            <left>60</left><top>36</top><width>56</width><height>56</height>
            <aspectratio>keep</aspectratio>
            <texture>$INFO[Window.Property(icon)]</texture>
        </control>
        <control type="label">
            <left>128</left><top>36</top><width>220</width><height>56</height>
            <aligny>center</aligny>
            <font>font30_title</font>
            <textcolor>{text}</textcolor>
            <label>Spotify</label>
        </control>
        <control type="list" id="100">
            <left>340</left><top>28</top><width>1180</width><height>72</height>
            <orientation>horizontal</orientation>
            <!-- moving down is handled by the window -->
            <ondown>100</ondown>
            <scrolltime>150</scrolltime>
            <itemlayout width="{nav_width}" height="72">{nav_item}
            </itemlayout>
            <focusedlayout width="{nav_width}" height="72">{nav_focused}
            </focusedlayout>
        </control>
        <control type="label">
            <right>60</right><top>28</top><width>360</width><height>40</height>
            <align>right</align>
            <font>font14</font>
            <textcolor>{text}</textcolor>
            <label>$INFO[System.Time]</label>
        </control>
        <control type="label">
            <right>60</right><top>66</top><width>360</width><height>32</height>
            <align>right</align>
            <font>font10</font>
            <textcolor>{dim}</textcolor>
            <label>$INFO[Window.Property(user)]</label>
            <visible>!Player.HasAudio</visible>
        </control>
        <control type="label">
            <right>60</right><top>66</top><width>360</width><height>32</height>
            <align>right</align>
            <font>font10</font>
            <textcolor>{accent}</textcolor>
            <scroll>true</scroll>
            <label>&#x266B;  $INFO[MusicPlayer.Title]$INFO[MusicPlayer.Artist, · ]</label>
            <visible>Player.HasAudio</visible>
        </control>
        <control type="group">
            <right>60</right><top>104</top><width>360</width><height>36</height>
            <visible>Player.HasAudio</visible>
            <control type="progress">
                <width>360</width><height>4</height>
                <texturebg colordiffuse="40FFFFFF">sp_white.png</texturebg>
                <midtexture colordiffuse="{accent}">sp_white.png</midtexture>
                <info>Player.Progress</info>
            </control>
            <control type="label">
                <top>8</top><width>360</width><height>28</height>
                <align>right</align>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[MusicPlayer.Time]$INFO[MusicPlayer.Duration, / ]</label>
            </control>
        </control>

        <!-- Rows -->
        <control type="grouplist" id="300">
            <left>0</left><top>590</top><width>1920</width><height>490</height>
            <orientation>vertical</orientation>
            <itemgap>10</itemgap>
            <scrolltime tween="sine">250</scrolltime>
            <onup>100</onup>
            <visible>String.IsEqual(Window.Property(view),tab)</visible>
            {rows}
        </control>

        <!-- Grid for categories, artists' albums and "show all" -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),grid)</visible>
            <control type="label">
                <left>80</left><top>130</top><width>1760</width><height>60</height>
                <font>font36_title</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[Window.Property(grid_title)]</label>
            </control>
            <control type="panel" id="600">
                <left>60</left><top>210</top><width>1800</width><height>870</height>
                <orientation>vertical</orientation>
                <onup>100</onup>
                <onright>602</onright>
                <pagecontrol>601</pagecontrol>
                <scrolltime tween="sine">250</scrolltime>
                <itemlayout width="{grid_width}" height="{tile_height}">{grid_item}
                </itemlayout>
                <focusedlayout width="{grid_width}" height="{tile_height}">{grid_focused}
                </focusedlayout>
            </control>{grid_scrollbar}
        </control>

        <!-- Track list of albums and playlists -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),tracks)</visible>
            <control type="image">
                <left>80</left><top>140</top><width>440</width><height>440</height>
                <texture border="12" colordiffuse="{tile}">sp_rounded.png</texture>
            </control>
            <control type="image">
                <left>80</left><top>140</top><width>440</width><height>440</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="sp_mask.png">$INFO[Window.Property(cover)]</texture>
            </control>
            <control type="textbox">
                <left>80</left><top>600</top><width>440</width><height>110</height>
                <font>font30_title</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[Window.Property(grid_title)]</label>
            </control>
            <control type="label">
                <left>80</left><top>712</top><width>440</width><height>36</height>
                <font>font12</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[Window.Property(tracks_info)]</label>
            </control>
            <control type="list" id="710">
                <left>80</left><top>770</top><width>440</width><height>170</height>
                <orientation>vertical</orientation>
                <onup>100</onup>
                <onright>700</onright>
                <itemlayout width="440" height="84">{button_item}
                </itemlayout>
                <focusedlayout width="440" height="84">{button_focused}
                </focusedlayout>
            </control>
            <control type="list" id="700">
                <left>600</left><top>130</top><width>1240</width><height>924</height>
                <orientation>vertical</orientation>
                <onup>100</onup>
                <onleft>710</onleft>
                <onright>702</onright>
                <pagecontrol>701</pagecontrol>
                <scrolltime tween="sine">200</scrolltime>
                <itemlayout width="1240" height="88">{track_item}
                </itemlayout>
                <focusedlayout width="1240" height="88">{track_focused}
                </focusedlayout>
            </control>{track_scrollbar}
        </control>

        <!-- Lyrics of the playing track -->
        <control type="group">
            <visible>String.IsEqual(Window.Property(view),lyrics)</visible>
            <control type="image">
                <width>1920</width><height>1080</height>
                <aspectratio>scale</aspectratio>
                <colordiffuse>30FFFFFF</colordiffuse>
                <texture background="true">$INFO[Player.Art(thumb)]</texture>
            </control>
            <control type="image">
                <top>580</top><width>1920</width><height>500</height>
                <texture>sp_gradient_bottom.png</texture>
            </control>
            <control type="image">
                <left>80</left><top>150</top><width>440</width><height>440</height>
                <texture border="12" colordiffuse="{tile}">sp_rounded.png</texture>
            </control>
            <control type="image">
                <left>80</left><top>150</top><width>440</width><height>440</height>
                <aspectratio>scale</aspectratio>
                <texture background="true" diffuse="sp_mask.png">$INFO[Player.Art(thumb)]</texture>
            </control>
            <control type="textbox">
                <left>80</left><top>612</top><width>440</width><height>110</height>
                <font>font30_title</font>
                <textcolor>{text}</textcolor>
                <label>$INFO[MusicPlayer.Title]</label>
            </control>
            <control type="label">
                <left>80</left><top>724</top><width>440</width><height>40</height>
                <font>font14</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[MusicPlayer.Artist]</label>
            </control>
            <control type="progress">
                <left>80</left><top>790</top><width>440</width><height>6</height>
                <texturebg colordiffuse="40FFFFFF">sp_white.png</texturebg>
                <midtexture colordiffuse="{accent}">sp_white.png</midtexture>
                <info>Player.Progress</info>
            </control>
            <control type="label">
                <left>80</left><top>806</top><width>440</width><height>32</height>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[MusicPlayer.Time]</label>
            </control>
            <control type="label">
                <left>80</left><top>806</top><width>440</width><height>32</height>
                <align>right</align>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[MusicPlayer.Duration]</label>
            </control>
            <control type="label">
                <left>80</left><bottom>40</bottom><width>440</width><height>32</height>
                <font>font10</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[Window.Property(lyrics_source)]</label>
            </control>
            <control type="label">
                <left>600</left><top>500</top><width>1240</width><height>60</height>
                <align>center</align>
                <font>font30_title</font>
                <textcolor>{dim}</textcolor>
                <label>$INFO[Window.Property(lyrics_message)]</label>
            </control>
            <control type="fixedlist" id="800">
                <left>600</left><top>130</top><width>1240</width><height>900</height>
                <orientation>vertical</orientation>
                <focusposition>2</focusposition>
                <onup>800</onup>
                <scrolltime tween="sine">500</scrolltime>
                <itemlayout width="1240" height="120">{lyrics_item}
                </itemlayout>
                <focusedlayout width="1240" height="120">{lyrics_focused}
                </focusedlayout>
            </control>
        </control>

        <!-- Status -->
        <control type="label">
            <left>80</left><bottom>40</bottom><width>1760</width><height>40</height>
            <align>center</align>
            <font>font13</font>
            <textcolor>{dim}</textcolor>
            <label>{loading}</label>
            <visible>String.IsEqual(Window.Property(loading),true)</visible>
            <animation effect="fade" start="100" end="40" time="800" pulse="true" condition="true">Conditional</animation>
        </control>
        <control type="label">
            <left>80</left><top>700</top><width>1760</width><height>60</height>
            <align>center</align>
            <font>font14</font>
            <textcolor>{dim}</textcolor>
            <label>$INFO[Window.Property(empty_label)]</label>
            <visible>String.IsEqual(Window.Property(empty),true) + !String.IsEqual(Window.Property(loading),true)</visible>
        </control>
    </controls>
</window>
"""


def generate_window():
    xml = WINDOW_TEMPLATE.format(
        background_dimmed="E6" + BACKGROUND[2:],
        tab_view="String.IsEqual(Window.Property(view),tab)",
        background=BACKGROUND,
        text=TEXT,
        dim=TEXT_DIM,
        accent=ACCENT,
        tile=TILE,
        nav_width=NAV_WIDTH,
        nav_item=nav_layout(False),
        nav_focused=nav_layout(True),
        rows="".join(row(index) for index in range(NUM_ROWS)),
        grid_width=GRID_WIDTH,
        tile_height=TILE_HEIGHT,
        grid_item=tile_layout(600, False, GRID_WIDTH),
        grid_focused=tile_layout(600, True, GRID_WIDTH),
        grid_scrollbar=scrollbar(601, 600, 602, 1870, 220, 850),
        track_item=track_layout(False),
        track_focused=track_layout(True),
        track_scrollbar=scrollbar(701, 700, 702, 1864, 140, 904),
        button_item=button_layout(False),
        button_focused=button_layout(True),
        lyrics_item=lyrics_layout(False),
        lyrics_focused=lyrics_layout(True),
        loading=localize(11110),
    )
    directory = os.path.dirname(XML_PATH)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(XML_PATH, "w", encoding="utf-8") as xml_file:
        xml_file.write(xml)


if __name__ == "__main__":
    generate_textures()
    generate_window()
    print("Generated", os.path.relpath(XML_PATH, ROOT))
