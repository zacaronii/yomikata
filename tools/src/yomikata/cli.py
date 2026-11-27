"""`yomikata` command line: identify images, validate the catalog, build the index."""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from pathlib import Path

from . import catalog, snapfile
from .disc import DiscError
from .identify import identify
from .layout import RAM_BASE


def _option(text: str) -> tuple[str, str]:
    key, eq, value = text.partition("=")
    if not (key and eq):
        raise argparse.ArgumentTypeError(f"{text!r} isn't KEY=VALUE")
    return key, value


def romfont_forms() -> tuple:
    from .romfont import FORMS
    return FORMS


def built_cores() -> Path | None:
    """The cores built into the harness image (tools/Dockerfile.harness), where it has them."""
    folder = Path(os.environ.get("YOMIKATA_BUILT_CORES", "/opt/yomikata/cores"))
    return folder if folder.is_dir() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yomikata", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("identify", help="print each image's platform, id and executable hash (JSON lines)")
    p.add_argument("images", nargs="+", type=Path)
    p.add_argument("--scratch", type=Path, help="where CHDs are unpacked (deleted after each image)")
    p = sub.add_parser("scan", help="sort disc images by how their script is stored (JSON lines; no text is kept)")
    p.add_argument("images", nargs="+", type=Path)
    p.add_argument("--scratch", type=Path, help="where CHDs are unpacked (deleted after each image)")
    p.add_argument("--samples", action="store_true", help="include the longest run per encoding (for review only)")
    p = sub.add_parser("draft", help="write draft entries from `scan` output (never overwrites); prints a summary")
    p.add_argument("results", type=Path, help="JSON lines from `yomikata scan` (a `path` field groups discs by folder)")
    p.add_argument("--titles", type=Path, help="JSON: folder or image name -> {title, title_romaji}")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("hooks", help="merge hook reports (an inbox's hooks/ folder) into entries; prints a summary")
    p.add_argument("reports", type=Path, help="folder with <platform>/<id>.json hook reports")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--min-lines", type=int, default=5)
    p.add_argument("--min-rate", type=float, default=0.8)
    p = sub.add_parser("locate", help="find OCR'd lines in RAM snapshots: in the standard encodings, else by their pattern of repeated characters")
    p.add_argument("snapshots", nargs="+", type=Path, help="<sha1>.ram.gz files from an inbox (their .json beside them)")
    p.add_argument("--show-hits", action="store_true", help="print each match's segment (script text: review only)")
    p.add_argument("--pattern", action="store_true", help="search by pattern even when a standard encoding matched")
    p = sub.add_parser("where", help="what holds the text on screen: the buffers the OCR'd lines stand in, tile windows, copies (no text printed)")
    p.add_argument("snapshots", nargs="+", type=Path, help="<sha1>.ram.gz files from an inbox (their .json beside them)")
    p.add_argument("--absent", nargs="*", default=[], type=Path, help="snapshots taken with other text or none: what they hold too is loaded script")
    p.add_argument("--table", action="store_true", help="also print the code=character pairs found (review only)")
    p.add_argument("--pattern", action="store_true", help="search by pattern even when a standard encoding fits")
    p.add_argument("--frames", type=int, default=2, help="how many snapshots with video memory and a frame to read by their tiles (default 2)")
    p.add_argument("--bpp", type=int, default=2, choices=(2, 4), help="tiles: bits a pixel of the text layer")
    p.add_argument("--mask", default="0x3FF", help="tiles: the bits of a cell that are the tile number")
    p = sub.add_parser("vram", help="PS1 video memory from a PCSX ReARMed savestate, saved as PNG views for finding fonts")
    p.add_argument("state", type=Path, help="<sha1>.state.gz from an inbox (or a raw savestate)")
    p.add_argument("--ram", type=Path, help="the snapshot's RAM (<sha1>.ram.gz), to confirm the layout")
    p.add_argument("-o", "--out", type=Path, required=True, help="prefix for <prefix>-rgb.png, <prefix>-nibbles.png, <prefix>.vram")
    p = sub.add_parser("font", help="a font out of video memory as a labelled sheet (scratch), and a table skeleton to fill in")
    p.add_argument("vram", type=Path, help="video memory: a snapshot's vram part, or the .vram `yomikata vram` wrote (gzipped or not)")
    p.add_argument("-o", "--out", type=Path, required=True, help="the sheet (PNG). The game's font: a scratch folder")
    p.add_argument("--bpp", type=int, default=2, choices=(2, 4), help="tiles: bits a pixel")
    p.add_argument("--packed", action="store_true", help="tiles: a nibble a pixel (Game Boy Advance) instead of bit planes")
    p.add_argument("--base", help="tiles: where they start in video memory (hex). Without it, found from --cells")
    p.add_argument("--cells", type=Path, help="tiles: a RAM snapshot holding a text buffer of tile numbers (see `yomikata where`)")
    p.add_argument("--at", help="tiles: the buffer's address (hex)")
    p.add_argument("--count", type=int, default=64, help="tiles: how many 2-byte cells of the buffer to look at")
    p.add_argument("--ram-base", help="address of the RAM snapshot's first byte (default: its .json)")
    p.add_argument("--mask", default="0x3FF", help="tiles: the bits of a cell that are the tile number (default 0x3FF)")
    p.add_argument("--first", default="0", help="first code on the sheet (hex); for a grid, the first cell's code")
    p.add_argument("--last", help="tiles: last tile on the sheet (hex)")
    p.add_argument("--tall", type=int, help="tiles: characters are two tiles tall, the lower half this many tiles on")
    p.add_argument("--grid", help="PS1: X,Y,WIDTH,HEIGHT,COLUMNS,ROWS of the cells in the 4-bit view (`yomikata vram`'s -nibbles.png)")
    p.add_argument("--tbl", type=Path, help="write a table skeleton: a line per code, filled where --known has it")
    p.add_argument("--unit", type=int, default=2, help="table: bytes per code")
    p.add_argument("--big-endian", action="store_true", help="table: codes are high byte first in memory")
    p.add_argument("--known", type=Path, help="table: HEX=character lines already found (`yomikata where --table`, another table)")
    p.add_argument("--attributes", default="0", help="table: the list once per value OR'd into the codes, e.g. 0,0x400,0x1800")
    p = sub.add_parser("replay", help="run a libretro core headless, from a savestate or from power-on; record RAM and frames (docs/replay.md)")
    p.add_argument("--core", type=Path, required=True, help="the libretro core (.so), e.g. pcsx_rearmed_libretro.so")
    p.add_argument("--game", type=Path, required=True, help="the game image (the one the savestate belongs to)")
    p.add_argument("--state", type=Path, help="savestate (<sha1>.state.gz from an inbox, or raw); without one the game boots")
    p.add_argument("--system", type=Path, help="the core's system folder, e.g. with your console's BIOS (default: <out>/system)")
    p.add_argument("--option", action="append", default=[], metavar="KEY=VALUE", type=_option,
                   help="a core option, e.g. pcsx_rearmed_memcard1=libretro for a game that wants a memory card (repeatable)")
    p.add_argument("--steps", help="a script instead of --press/--frames, e.g. 'wait:600,start:4,30x(a:4,wait:40,snap)'")
    p.add_argument("--watch", default="", help="with --steps: addresses (hex, comma separated) whose byte is printed at each snap")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM addresses --watch takes")
    p.add_argument("--frames", type=int, default=120, help="frames to record")
    p.add_argument("--press", default="", help="buttons to press first, e.g. 'a' to advance a line (RetroPad names)")
    p.add_argument("--wait", type=int, default=10, help="frames to run before pressing")
    p.add_argument("-o", "--out", type=Path, required=True, help="folder for ram.npy, frames.npy and PNGs every 10 frames; with --steps, for the snaps")
    p = sub.add_parser("contrast", help="bytes that hold one value in some RAM snapshots and never in the others (finds flags)")
    p.add_argument("--same", nargs="+", type=Path, required=True, help="snapshots where it holds (.ram.gz or raw)")
    p.add_argument("--other", nargs="+", type=Path, required=True, help="snapshots where it doesn't")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM the snapshots are")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("find", help="where a string stands in RAM snapshots, in the standard encodings")
    p.add_argument("text", help="text read off the frame (it isn't stored anywhere)")
    p.add_argument("snapshots", nargs="+", type=Path, help=".ram.gz or raw")
    p.add_argument("--absent", nargs="+", type=Path, default=[], help="snapshots of moments without the text: places that hold it there too are skipped")
    p.add_argument("--platform", default="ps1", choices=sorted(RAM_BASE), help="whose RAM the snapshots are")
    p = sub.add_parser("layout", help="read an entry's layout from a RAM snapshot (checks a layout before a PR)")
    p.add_argument("entry", type=Path, help="games/<platform>/<id>.json")
    p.add_argument("ram", type=Path, help="RAM snapshot (<sha1>.ram.gz from an inbox, or raw)")
    p.add_argument("--base", default=None, help="address of the snapshot's first byte (default: its .json, else the platform's RAM)")
    p.add_argument("--version", help="exe_sha1 to pick the layout for (default: its .json's, else the only version of its id, else the general one)")
    p.add_argument("--root", type=Path, default=Path("."), help="catalog root, for table charsets")
    p = sub.add_parser("check", help="read an entry's layout from many snapshots: counts, and a page of each reading beside its frame")
    p.add_argument("entry", type=Path, help="games/<platform>/<id>.json")
    p.add_argument("snapshots", nargs="+", type=Path, help="RAM snapshots (their .json and frame beside them, if any)")
    p.add_argument("-o", "--out", type=Path, help="folder for report.html (game text and frames: a scratch folder)")
    p.add_argument("--base", default=None, help="address of the snapshots' first byte (default: their .json, else the platform's RAM)")
    p.add_argument("--version", help="exe_sha1 or header_crc32 to pick the layout for")
    p.add_argument("--root", type=Path, default=Path("."), help="catalog root, for table charsets")
    p = sub.add_parser("run", help="run a script over snapshots with the reader runtime; prints what it emits (JSON lines of game text: not for the catalog)")
    p.add_argument("script", type=Path, help="games/<platform>/<id>.lua (its data files beside it)")
    p.add_argument("snapshots", nargs="+", type=Path, help="RAM snapshots (their .json and .vram.gz beside them, if any)")
    p.add_argument("--platform", help="whose memory the snapshots are (default: their .json, else the script's folder)")
    p.add_argument("--base", default=None, help="address of the snapshots' first byte (default: their .json, else the platform's RAM)")
    p.add_argument("--frames", type=int, default=2, help="ticks over each snapshot")
    p.add_argument("--sequence", action="store_true", help="the snapshots are consecutive moments of one run: one reader, one tick each")
    p.add_argument("--rom", type=Path, help="the game's file, unpacked: a script then finds the cartridge where it reads it in play (snapshots keep memory only)")
    p.add_argument("--log", action="store_true", help="print the script's log lines too")
    p.add_argument("--story", action="store_true", help="only what is read: dialogue and choices, not interface text")
    p.add_argument("--dump", action="store_true", help="everything the game has in memory at each snapshot, as the script's own dump function says it: for reading a place's whole text through the script's decoder")
    p.add_argument("--containing", default="", help="with --dump: only, for each of these characters, the two shortest texts that have it (a doubtful reading shows in its sentence)")
    p.add_argument("--unconfirmed", type=Path, help="with --dump: a file of the characters already confirmed in a sentence; prints the texts that hold a kanji not among them, each such kanji in 【 】, shortest first: a place's text to read through")
    p = sub.add_parser("bundle", help="a script as it is run and published: one file, with the lib/ modules it asks for inside it")
    p.add_argument("script", type=Path, help="games/<platform>/<id>.lua")
    p.add_argument("-o", "--out", type=Path, help="write it here (default: print it)")
    p = sub.add_parser("compare", help="a script beside its entry's layout on the same snapshots: how many read the same (counts only)")
    p.add_argument("script", type=Path, help="games/<platform>/<id>.lua")
    p.add_argument("entry", type=Path, help="games/<platform>/<id>.json")
    p.add_argument("snapshots", nargs="+", type=Path, help="RAM snapshots (an inbox's, or a harness's snaps)")
    p.add_argument("--base", default=None, help="address of the snapshots' first byte (default: their .json, else the platform's RAM)")
    p.add_argument("--version", help="exe_sha1 or header_crc32 to pick the layout for, when the snapshots don't say")
    p.add_argument("--frames", type=int, default=2, help="ticks the script gets over each snapshot")
    p.add_argument("--show", action="store_true", help="print what differs, both readings (game text: for a terminal, not the catalog)")
    p = sub.add_parser("propose", help="draft a layout for text kept as tiles, from snapshots with video memory and a frame; read it back")
    p.add_argument("snapshots", nargs="+", type=Path, help="RAM snapshots with <name>.vram.gz and a frame beside them (an inbox's, or `replay` snaps)")
    p.add_argument("--table", action="append", default=[], help="a table file to read the draft back with (repeatable), relative to --root")
    p.add_argument("--unit", type=int, default=2, help="bytes per code of the table")
    p.add_argument("--platform", default="sfc", choices=sorted(RAM_BASE), help="whose RAM the snapshots are, when they have no .json")
    p.add_argument("--bpp", type=int, default=2, choices=(2, 4), help="bits a pixel of the text layer's tiles")
    p.add_argument("--mask", default="0x3FF", help="the bits of a cell that are the tile number")
    p.add_argument("--kind", help="draft this window instead of the best: WIDTHxROWS as listed, e.g. 17x3")
    p.add_argument("--root", type=Path, default=Path("."), help="catalog root, for --table")
    p.add_argument("-o", "--out", type=Path, help="write the draft's layouts here (JSON)")
    p = sub.add_parser("new-script", help="write the skeleton of a script and, where the game has none, of its entry; prints a test to start from")
    p.add_argument("platform", help="the catalog's name for the console: ps1, gc, sfc, …")
    p.add_argument("id", help="the game's id, as `yomikata identify` gives it")
    p.add_argument("--title", required=True, help="the game's title as on its box, with a romanised one in brackets")
    p.add_argument("--build", required=True, help="the build it is written on: exe SHA-1, header CRC-32, GameCube id-version, or PS2 serial-CRC")
    p.add_argument("--check", default="", help="the Lua lines of its build check, as the harness's suggest_build_words gives them")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("expand", help="rewrite harness snaps kept as what differs from another as whole snapshots (for a tool that doesn't know the form)")
    p.add_argument("snapshots", nargs="+", type=Path)
    p = sub.add_parser("glyphs", help="a game's own font from a snapshot with video memory: a sheet of the glyphs still to read (scratch), and each glyph's fingerprint and character where a dictionary has it")
    p.add_argument("snapshot", type=Path, help="a RAM snapshot with its <name>.vram.gz beside it (a harness snap)")
    p.add_argument("--list", required=True, help="address of the glyph records (four bytes each), hex")
    p.add_argument("--count", required=True, help="how many records: a number, or @ADDRESS of a 32-bit count")
    p.add_argument("--base", default="0x80000000", help="address of the snapshot's first byte (default 0x80000000)")
    p.add_argument("--page", default="896,256", help="the texture page's corner in video memory, as x,y words")
    p.add_argument("--height", type=int, default=13, help="a glyph's rows (default 13)")
    p.add_argument("--dictionary", type=Path, help="fingerprint=character lines already read")
    p.add_argument("--sheet", type=Path, help="write the glyphs the dictionary hasn't got as a PNG, to scratch")
    p.add_argument("--all", action="store_true", help="put every glyph on the sheet, read or not")
    p = sub.add_parser("rom-font", help="a game's whole font out of its cartridge: where it is (from a picture of the game's text), numbered sheets of its glyphs in code order (scratch), and the rows read off them written into a table, held against what was read in play")
    p.add_argument("rom", type=Path, help="the cartridge: a file, or a .zip holding it")
    p.add_argument("--find", type=Path, help="a picture of the game's text (a snap's PNG): look for pieces of it in the cartridge, to learn where the font is and in which form")
    p.add_argument("--box", help="with --find: X0,Y0,X1,Y1 of the text in the picture (default: all of it)")
    p.add_argument("--ink", type=int, default=120, help="with --find: a pixel is ink when its brightest channel is above this (default 120: light text on dark; a minus sign for dark on light, e.g. -100)")
    p.add_argument("--at", help="where the first glyph is in the cartridge (hex)")
    p.add_argument("--first", default="0", help="the first glyph's code (hex)")
    p.add_argument("--count", type=int, default=256, help="how many glyphs (default 256)")
    p.add_argument("--form", default="tiles4", choices=romfont_forms(), help="tiles4 (four 8x8 tiles of 4 bits a pixel: a 16x16 glyph), rows1 (rows of a bit a pixel, each in whole bytes), bits1 (a bit a pixel, rows packed: 12-pixel rows), snes2 or snes4 (Super Famicom tiles of 2 or 4 bits a pixel, a glyph's tiles in reading order)")
    p.add_argument("--width", type=int, default=16, help="rows1, bits1, snes2, snes4: pixels a row")
    p.add_argument("--height", type=int, default=16, help="rows1, bits1, snes2, snes4: rows")
    p.add_argument("--sheet", type=Path, help="write the glyphs as a numbered PNG, to scratch; blank glyphs take no room in a row's reading")
    p.add_argument("--columns", type=int, default=16, help="glyphs a row of the sheet (default 16)")
    p.add_argument("--scale", type=int, default=3, help="enlarge the sheet's glyphs (default 3)")
    p.add_argument("--json", type=Path, help="write the glyphs for `yomikata glyph-read` (a code to its rows), to scratch")
    p.add_argument("--glyphs", type=Path, help="write a dictionary of the glyphs' pictures (lib/glyphs.lua, lib/cells.lua): each glyph --chars has, by the hash of its picture as lib/cells.lua cuts one, and a `# widths` line")
    p.add_argument("--chars", type=Path, help="with --glyphs: the characters, a line a glyph: its code in hex (as on the sheet), = and the character")
    p.add_argument("--cut", help="with --glyphs: X0:X1, the columns of each glyph the game draws (a font that keeps two pictures side by side)")
    p.add_argument("--fixed", action="store_true", help="with --glyphs: a font drawn on a fixed grid: glyphs kept whole, not trimmed to their ink")
    p.add_argument("--read", type=Path, help="the rows read off the sheets (scratch): a line is a code in hex, then the characters from that glyph on, ? for one not read")
    p.add_argument("--known", type=Path, help="with --read: a table read in play (HEX=character lines) to hold the readings against")
    p.add_argument("--unit", type=int, default=2, help="table: bytes a code (default 2)")
    p.add_argument("--big-endian", action="store_true", help="table: codes are high byte first in memory")
    p.add_argument("--tbl", type=Path, help="with --read: write the table, what was read in play first and unchanged, then the sheet's readings for the other codes")
    p.add_argument("--lead", help="codes below this (hex) are one byte, and the glyphs after them two: the lead byte, then a byte (Xenogears: F8). Codes are then the game's own on the sheet, in --first, --read and the table")
    p.add_argument("--locate", help="FILE@HEX: where stretches of a memory snap from HEX on lie in the cartridge or disc (a font found in memory with --find, then on the disc)")
    p = sub.add_parser("glyph-read", help="a second reader (manga-ocr, from a scratch environment) over a font's glyphs: its readings beside yours, where they differ, and those in a sentence of the game's text")
    p.add_argument("glyphs", type=Path, help="the glyphs as the harness's glyph_pairs writes them: a .json of code to rows")
    p.add_argument("--rows", type=int, required=True, help="rows of a glyph")
    p.add_argument("--row-bytes", type=int, default=2, help="bytes a row (default 2)")
    p.add_argument("--mine", type=Path, help="your readings: a table (HEX=character lines); codes as in the glyphs file")
    p.add_argument("--text", type=Path, help="the game's text, a line a message (scratch): each differing reading is shown in a sentence")
    p.add_argument("--out", type=Path, required=True, help="where to write the second reader's readings (code, a tab, the reading)")
    p = sub.add_parser("font-read", help="read a whole font glyph by glyph: a second reader's readings (manga-ocr, from a scratch environment), sheets of each glyph beside its reading and of each correction beside its glyph, the table, and the characters given to two glyphs")
    p.add_argument("font", type=Path, help="the cartridge, disc, or a font file taken out of a card (nitro)")
    p.add_argument("--at", required=True, help="where the font's first glyph is (hex), as rom-font draws it")
    p.add_argument("--first", default="0", help="its code (hex)")
    p.add_argument("--count", type=int, required=True, help="glyphs")
    p.add_argument("--form", default="tiles4", help="the glyphs' form, as rom-font's --form (default tiles4)")
    p.add_argument("--width", type=int, default=16)
    p.add_argument("--height", type=int, default=16)
    p.add_argument("--lead", help="as rom-font's: codes below this (hex) are one byte")
    p.add_argument("--ocr", type=Path, help="read every glyph with the second reader now and write its readings here (code, a tab, the reading); needs manga-ocr")
    p.add_argument("--readings", type=Path, help="the second reader's readings written by --ocr before")
    p.add_argument("--rows", type=Path, help="readings off rom-font's sheets as rows (a code and the characters from it on), given before the second reader's: for signs and kana")
    p.add_argument("--fix", type=Path, help="corrections: HEX=character a line, HEX= for a glyph that is no character")
    p.add_argument("--pairs", type=Path, help="a scratch folder: sheets of every glyph beside its reading, to find what is wrong")
    p.add_argument("--check", type=Path, help="a scratch folder: sheets of each correction beside its glyph, and of the characters given to two glyphs")
    p.add_argument("--draw", type=Path, help="a font file (TTF/OTF) with the characters to draw the readings on the sheets in (without one, Pillow's own, which has no kanji)")
    p.add_argument("--tbl", type=Path, help="write the table (HEX=character a line; codes as rom-font's --tbl writes them)")
    p.add_argument("--unit", type=int, default=2, help="table: bytes a code (default 2)")
    p.add_argument("--big-endian", action="store_true", help="table: codes are high byte first in memory")
    p = sub.add_parser("unread", help="the pictures a script logged for want of a reading (lib/glyphs.lua; the harness keeps them in scratch): a sheet of those the dictionary hasn't got, the second reader's readings, and readings written into the dictionary")
    p.add_argument("logs", type=Path, nargs="+", help="files with the logged lines: scratch's glyphs/<script>.log, or a host's log")
    p.add_argument("--dictionary", type=Path, required=True, help="the script's dictionary: games/<platform>/<id>.glyphs")
    p.add_argument("--sheet", type=Path, help="write the unread pictures as a PNG, to scratch; and beside it, as <name>-rows.png, the rows of text the script logged them in")
    p.add_argument("--columns", type=int, default=16, help="pictures a row of the sheet (default 16)")
    p.add_argument("--font", type=Path, help="a font file for the characters a logged row has as text (a script that knows part of a row by code): they are drawn with it on the rows' sheet and for the second reader")
    p.add_argument("--each", type=int, default=2, help="rows drawn for each unread picture (default 2)")
    p.add_argument("--most", type=int, default=0, help="read and draw only the first so many unread pictures, as logged (default: all)")
    p.add_argument("--second", type=Path, help="a scratch file of the second reader's readings (hash, a tab, what it takes the picture for alone, then for each logged row it read to the right length, the character there): those it lacks are read now, where manga-ocr is installed")
    p.add_argument("--row", nargs="+", default=[], metavar="NUMBER=TEXT", help="a row read whole, by its number on the rows' sheet: each of its pictures takes the character that stands there (spaces don't count)")
    p.add_argument("--add", nargs="+", default=[], metavar="HASH=CHARACTER", help="readings into the dictionary, each by the start of its hash (four digits as on the sheet); HASH= takes one out")
    p = sub.add_parser("sorted", help="check a font's readings by the order its glyphs stand in (JIS, Shift-JIS, Unicode): which readings break it, and what the order allows for each glyph not yet sure")
    p.add_argument("readings", type=Path, help="a line for each glyph in the font's order: its reading, or ? (anything after the first character is a note)")
    p.add_argument("--order", default="jis", help="jis (default), sjis or unicode")
    p.add_argument("--all", action="store_true", help="print the glyphs that are in order as well")
    p = sub.add_parser("nitro", help="a DS card's files (NitroFS): listed with their sizes and packing (JSON lines, names only), taken out to a scratch folder, LZ77 (0x10, 0x11) undone")
    p.add_argument("card", type=Path, help="the card image (.nds, or a .zip with one)")
    p.add_argument("--only", action="append", default=[], help="a pattern of paths (fnmatch: *.bin, font/*); several may be given")
    p.add_argument("--extract", type=Path, help="a scratch folder to write the files into, by their paths")
    p.add_argument("--unpack", action="store_true", help="with --extract: a packed file is written unpacked (and its streams, with --streams)")
    p.add_argument("--streams", action="store_true", help="list the packed streams inside each file (an archive of sections); with --extract --unpack, write each as <path>@<offset>.bin")
    p = sub.add_parser("rvz", help="a GameCube or Wii disc's files out of an RVZ or WIA image (or a plain .iso/.gcm): listed with their sizes (JSON lines, names only), taken out to a scratch folder")
    p.add_argument("disc", type=Path, help="the image: .rvz, .wia, .iso or .gcm")
    p.add_argument("--only", action="append", default=[], help="a pattern of paths (fnmatch: res/Msg/*); several may be given")
    p.add_argument("--extract", type=Path, help="a scratch folder to write the files into, by their paths")
    p = sub.add_parser("placements", help="where a game puts its people, doors and things, read off its disc: targets in its own world coordinates for the harness's walk_3d and meet (JSON lines: names and places only). GameCube Zelda stages first")
    p.add_argument("disc", type=Path, help="the disc image (.rvz, .wia, .iso, .gcm)")
    p.add_argument("archive", help="the stage or room archive on the disc, e.g. res/Stage/sea/Room44.arc")
    p.add_argument("--name", action="append", default=[], help="only entries whose name matches (fnmatch); several may be given")
    p.add_argument("--chunk", action="append", default=[], help="only entries of these chunks (ACTR, ACT0, TGDR, PLYR, …; fnmatch)")
    p = sub.add_parser("route", help="a way over a game's own ground, from where the character stands to places on the disc's stage, round its cliffs, walls and drops: meet's targets, each with its waypoints (JSON lines: names and places only). GameCube Zelda collision (.dzb) first")
    p.add_argument("disc", type=Path, help="the disc image (.rvz, .wia, .iso, .gcm)")
    p.add_argument("archive", help="the stage or room archive with the collision and the placements, e.g. res/Stage/sea/Room44.arc")
    p.add_argument("--from", dest="frm", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"), help="where the character stands (walk_3d says x and z, its height y)")
    p.add_argument("--to", type=float, nargs=3, action="append", default=[], metavar=("X", "Y", "Z"), help="a place to go to; several may be given")
    p.add_argument("--name", action="append", default=[], help="placed entries of the archive whose name matches (fnmatch), as places to go to")
    p.add_argument("--chunk", action="append", default=[], help="with --name: only entries of these chunks")
    p.add_argument("--low", type=float, help="floors lower than this aren't walked (under water; Wind Waker: 1)")
    p.add_argument("--up", type=float, default=30.0, help="the highest step walked up (default 30)")
    p.add_argument("--down", type=float, default=60.0, help="the deepest step walked down (default 60)")
    p.add_argument("--slope", type=float, default=50.0, help="the steepest ground walked, degrees (default 50)")
    p.add_argument("--cell", type=float, default=25.0, help="the side of a cell of the ground (default 25)")
    p.add_argument("--skip", action="append", default=[], help="collision groups to leave out (fnmatch)")
    p.add_argument("--reach", type=float, default=600.0, help="how far from each end a floor is looked for (default 600)")
    p = sub.add_parser("disc-text", help="the stretches of a disc image that read as text through a table of the game's codes (the game's text: write it to scratch)")
    p.add_argument("disc", type=Path)
    p.add_argument("--table", type=Path, action="append", required=True, help="a .tbl of the game's codes; several are read as one")
    p.add_argument("--least", type=int, default=8, help="the fewest characters on end (default 8)")
    p.add_argument("--out", type=Path, required=True, help="a file in a scratch folder: an offset and a text a line")
    p = sub.add_parser("validate", help="check entries against the schema and the catalog rules")
    p.add_argument("--root", type=Path, default=Path("."))
    p = sub.add_parser("index", help="write index.json; with --check, fail if it's out of date")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--check", action="store_true")
    p = sub.add_parser("harness", help="the emulator harness as an MCP server on stdin and stdout (docs/harness.md); run it in its container")
    p.add_argument("--games", type=Path, default=Path("/games"), help="the library's game folders (read)")
    p.add_argument("--cores", type=Path, default=Path("/cores"), help="libretro cores (read)")
    p.add_argument("--inbox", type=Path, default=Path("/inbox"), help="an inbox's snapshots folder, for readers' savestates (read)")
    p.add_argument("--scratch", type=Path, default=Path("/scratch"), help="states, snaps and the cores' own files (written)")
    p.add_argument("--system", type=Path, help="where a core looks for a BIOS (read; default: system/ under scratch)")
    p.add_argument("--allow-poke", action="store_true",
                   help="offer the poke tool: writes into the game's memory, to reach a moment (docs/harness.md)")
    p.add_argument("--call-seconds", type=float, default=120.0,
                   help="how long one call may run the game before it stops and says so (default 120); a call "
                        "that hasn't come back a minute after that ends the harness")
    p = sub.add_parser("play", help="run a plan of the harness's tools on a game without an agent, for playing it outside an MCP session; each of its calls is a call in scratch's record (offline_play) and counts against the game's budget (docs/harness.md)")
    p.add_argument("plan", type=Path, help="a JSON list of [tool, arguments] as the harness takes them, the first a load")
    p.add_argument("--games", type=Path, default=Path("/games"), help="the library's game folders (read)")
    p.add_argument("--cores", type=Path, default=Path("/cores"), help="libretro cores (read)")
    p.add_argument("--inbox", type=Path, default=Path("/inbox"), help="an inbox's snapshots folder (read)")
    p.add_argument("--scratch", type=Path, default=Path("/scratch"), help="states, snaps and the record (written)")
    p.add_argument("--system", type=Path, help="where a core looks for a BIOS (read; default: system/ under scratch)")
    p.add_argument("--out", type=Path, help="a folder for the pictures the tools answer with")
    p.add_argument("--call-seconds", type=float, default=120.0, help="how long one of the plan's calls may run the game (default 120)")
    p = sub.add_parser("calls", help="the harness's record of calls summed by game: calls, runs of the harness, failures, by tool (JSON lines)")
    p.add_argument("record", type=Path, help="scratch's record/calls.jsonl, or the scratch folder")
    p.add_argument("--game", help="only games whose path has this in it")
    p.add_argument("--trend", action="store_true", help="instead, what each game's script cost up to the comparison that met its bar (calls, runs, minutes on the clock), as a Markdown table for a pull request: the games side by side, oldest first")
    p = sub.add_parser("regress", help="the nightly regression check: every merged entry's bar screens read again with the current script, against a baseline (docs/regress.md); names and numbers only, never the text")
    p.add_argument("--catalog", type=Path, default=Path("."), help="the catalog at main (default: here)")
    p.add_argument("--games", type=Path, default=Path("/games"), help="the library (read)")
    p.add_argument("--scratch", type=Path, default=Path("/scratch"), help="the harness's scratch: its snaps, and regress/ for what this keeps")
    p.add_argument("--accept", action="append", default=[], metavar="ID", help="take this entry's fallen readings as its new baseline (after checking them)")
    p.add_argument("--frames", type=int, default=40, help="ticks over each snap (as compare's)")
    p.add_argument("--summary", type=Path, help="on a fall, write one line for a notifier here (counts and snap names)")
    p = sub.add_parser("serve", help="run the sample inbox (needs the server extra; settings from YOMIKATA_* env)")
    p.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    if args.cmd == "harness":
        from .harness import mcp
        from .harness.machine import Machine
        from .harness.session import Roots, Session
        session = Session(Roots(args.games, args.cores, args.inbox, args.scratch, args.system, built_cores()), Machine)
        session.call_seconds = max(1.0, args.call_seconds)
        session.allow_poke = args.allow_poke
        mcp.serve(session)
        # The core has ended (or been left): leave without the process's own teardown, which some cores'
        # libraries never come back from (flycast's kept the harness until its timeout).
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    if args.cmd == "play":
        from .harness import play
        from .harness.machine import Machine
        from .harness.session import HarnessError, Roots, Session
        session = Session(Roots(args.games, args.cores, args.inbox, args.scratch, args.system, built_cores()), Machine)
        session.call_seconds = max(1.0, args.call_seconds)
        from .harness.mcp import finish
        try:
            lines = play.run(session, json.loads(args.plan.read_text(encoding="utf-8")), args.out)
        except HarnessError as e:
            print(str(e), file=sys.stderr)
            lines = None
        finally:
            finish(session)
        if lines is not None:
            print(json.dumps({"recorded": len(lines), "frames": lines[-1]["outcome"]["frames"] if lines else 0},
                             ensure_ascii=False))
        sys.stdout.flush()
        sys.stderr.flush()
        # As the harness: no teardown of the process, which some cores' libraries never come back from.
        os._exit(0 if lines and all(e["ok"] for e in lines) else 1)

    if args.cmd == "calls":
        from .calls import summary
        record = args.record / "record" / "calls.jsonl" if args.record.is_dir() else args.record
        if not record.is_file():
            print(f"no record of calls at {record}", file=sys.stderr)
            return 1
        if args.trend:
            from .calls import table, trend
            print(table([row for row in trend(record) if not args.game or args.game in row["game"]]))
            return 0
        for row in summary(record):
            if not args.game or args.game in (row["game"] or ""):
                print(json.dumps(row, ensure_ascii=False))
        return 0

    if args.cmd == "serve":
        from .inbox import serve
        serve(port=args.port)
        return 0

    if args.cmd == "new-script":
        from .newscript import new_script
        try:
            written, test = new_script(args.root, args.platform, args.id, args.title, args.build, args.check)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        for path in written:
            print(f"wrote {path}", file=sys.stderr)
        print(test)
        return 0

    if args.cmd == "expand":
        try:
            done = sum(1 for p in args.snapshots if snapfile.expand(p))
        except (snapfile.SnapError, OSError) as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"{done} of {len(args.snapshots)} rewritten whole")
        return 0

    if args.cmd == "glyphs":
        import struct
        from . import glyphs as gl
        ram = snapfile.read(args.snapshot)
        video = args.snapshot.with_name(args.snapshot.name.split(".")[0] + ".vram.gz")
        if not video.is_file():
            print(f"{video.name} isn't beside the snapshot: a font's pictures are in video memory", file=sys.stderr)
            return 1
        base, at = int(args.base, 16), int(args.list, 16)
        count = (struct.unpack_from("<I", ram, int(args.count[1:], 16) - base)[0] if args.count.startswith("@")
                 else int(args.count, 0))
        if not 0 < count <= 4096 or not 0 <= at - base <= len(ram) - 4 * count:
            print("the glyph list isn't in the snapshot", file=sys.stderr)
            return 1
        x, y = (int(v) for v in args.page.split(","))
        pixels = gl.page(snapfile.read(video), x, y)
        found = [gl.picture(pixels, rec, args.height) for rec in gl.records(ram[at - base:at - base + 4 * count], count)]
        known = gl.read_dictionary(args.dictionary.read_text(encoding="utf-8")) if args.dictionary and args.dictionary.is_file() else {}
        listed, unread = gl.sort_out(found, known)
        read = [(character, found[number]) for number, _, character in listed if character is not None]
        for number, mark, character in listed:
            near = "" if character is not None or mark == "0" * 16 else "  like: " + " ".join(
                f"{c} {score:.2f}" for c, score in gl.nearest(found[number], read))
            print(f"{number:03X} {mark} {character if character is not None else '?'}{near}")
        for character, marks in gl.twice(known).items():
            print(f"{character} is given to {len(marks)} pictures in the dictionary: {' '.join(marks)}", file=sys.stderr)
        shown = list(range(count)) if args.all else unread
        if args.sheet and shown:
            gl.sheet([found[n] for n in shown], [f"{n:03X}" for n in shown]).save(args.sheet)
        print(f"{count} glyphs, {sum(1 for _, _, c in listed if c is not None)} in the dictionary, "
              f"{len(unread)} to read" + (f": {args.sheet}" if args.sheet and shown else ""), file=sys.stderr)
        return 0

    if args.cmd == "rom-font":
        import json as _json
        import numpy as np
        from . import font as fonts
        from . import romfont
        rom = romfont.cartridge(args.rom)
        lead = int(args.lead, 16) if args.lead else None
        if args.locate:
            name, _, at = args.locate.rpartition("@")
            for row in romfont.locate(rom, Path(name).read_bytes(), int(at, 16)):
                print(_json.dumps(row))
            return 0
        if args.find:
            from PIL import Image
            picture = np.asarray(Image.open(args.find).convert("RGB")).astype(int).max(axis=2)
            ink = picture > args.ink if args.ink >= 0 else picture < -args.ink
            if args.box:
                x0, y0, x1, y1 = (int(v) for v in args.box.split(","))
                ink = ink[y0:y1, x0:x1]
            for row in romfont.find(rom, ink):
                print(_json.dumps(row))
            return 0
        if not args.at:
            print("give --at (where the first glyph is), or --find with a picture of the game's text", file=sys.stderr)
            return 2
        first = int(args.first, 16)
        glyphs = romfont.glyphs(rom, int(args.at, 16), first, args.count, args.form, args.width, args.height, lead)
        blanks = {code for code, pixels in glyphs.items() if not pixels.any()}
        inked = {code: pixels for code, pixels in glyphs.items() if code not in blanks}
        if args.sheet:
            # On the sheet a glyph keeps its code's column, so a row is read against its numbers.
            fonts.sheet({code: 1 - pixels for code, pixels in inked.items()}, args.columns, args.scale, background=1).save(args.sheet)
        if args.json:
            args.json.write_text(_json.dumps({f"{code:X}": romfont.rows_hex(pixels) for code, pixels in inked.items()}),
                                 encoding="utf-8")
        print(f"{len(glyphs)} glyphs from 0x{first:X}, {len(blanks)} of them blank", file=sys.stderr)
        if args.glyphs:
            if not args.chars:
                print("--glyphs needs --chars (which character each glyph is)", file=sys.stderr)
                return 2
            characters = {}
            for line in args.chars.read_text(encoding="utf-8").splitlines():
                code, sep, ch = line.partition("=")
                if sep and not line.startswith("#") and len(ch) == 1:
                    characters[int(code, 16)] = ch
            cut = tuple(int(v) for v in args.cut.split(":")) if args.cut else None
            lines, widths = romfont.dictionary(glyphs, characters, cut, not args.fixed)
            args.glyphs.write_text(f"# widths {' '.join(map(str, widths))}\n" + "\n".join(lines) + "\n", encoding="utf-8")
            print(f"{len(lines)} pictures written to {args.glyphs}", file=sys.stderr)
        if args.read:
            try:
                read = romfont.readings(args.read.read_text(encoding="utf-8"), blanks, lead)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 1
            text = args.known.read_text(encoding="utf-8") if args.known else ""
            if lead is None:
                known = fonts.known_pairs(text, args.unit, args.big_endian)
            else:                                   # a byte, or the lead byte and one more, as the text has them
                known = {**fonts.known_pairs(text, 1), **fonts.known_pairs(text, 2, True)}
            print(_json.dumps(romfont.held(read, known), ensure_ascii=False))
            if args.tbl:
                order = "big" if args.big_endian or lead is not None else "little"
                key = lambda code: code.to_bytes(1 if lead is not None and code < lead else args.unit,  # noqa: E731
                                                 order).hex().upper()
                lines = [f"{key(code)}={ch}" for code, ch in sorted(read.items()) if code not in known]
                args.tbl.write_text("\n".join(lines) + "\n", encoding="utf-8")
                print(f"{len(lines)} codes not read in play written to {args.tbl}", file=sys.stderr)
        return 0
    if args.cmd == "glyph-read":
        from . import glyphread
        from . import tbl as tables
        glyphs = glyphread.pictures(args.glyphs, args.rows, args.row_bytes)
        try:
            second = glyphread.read(glyphs)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 2
        args.out.write_text("".join(f"{code}\t{second[code]}\n" for code in sorted(second)), encoding="utf-8")
        if not args.mine:
            print(f"{len(second)} glyphs read, written to {args.out}", file=sys.stderr)
            return 0
        mine = {code.hex().upper(): ch for code, ch in tables.parse(args.mine.read_text(encoding="utf-8")).chars.items()}
        rows = glyphread.compare({c: mine[c] for c in second if c in mine}, second)
        differ = [r for r in rows if r["status"] == "differ"]
        texts = args.text.read_text(encoding="utf-8").splitlines() if args.text else []
        found = glyphread.sentences(texts, "".join(r["first"] for r in differ)) if texts else {}
        for r in rows:
            if r["status"] != "agree":
                where = " | ".join(found.get(r["first"] or "", [])) if r["status"] == "differ" else ""
                print(f"{r['code']} {r['first'] or '?'} {r['status']}: the second reader says {r['second']}" + (f"  {where}" if where else ""))
        counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("agree", "differ", "unread")}
        print(f"{len(rows)} glyphs: {counts['agree']} read the same two ways, {counts['differ']} that differ (read those in a "
              f"sentence), {counts['unread']} you haven't read", file=sys.stderr)
        return 0
    if args.cmd == "font-read":
        from . import fontread
        from . import romfont as fonts
        lead = int(args.lead, 16) if args.lead else None
        glyphs = fonts.glyphs(fonts.cartridge(args.font), int(args.at, 16), int(args.first, 16), args.count, args.form,
                              args.width, args.height, lead)
        if args.ocr:
            from .glyphread import second_reader
            try:
                reader = second_reader()
            except RuntimeError as e:
                print(e, file=sys.stderr)
                return 2
            readings = fontread.ocr(glyphs, reader)
            fontread.write_readings(args.ocr, readings)
            print(f"{len(readings)} glyphs read, written to {args.ocr}", file=sys.stderr)
        else:
            readings = fontread.load_readings(args.readings) if args.readings else {}
        rows = fonts.readings(args.rows.read_text(encoding="utf-8"), lead=lead) if args.rows else {}
        fixes = fontread.corrections(args.fix.read_text(encoding="utf-8")) if args.fix else {}
        table = fontread.resolved({c: t for c, t in readings.items() if c in glyphs}, rows, fixes)
        if args.pairs:
            pages = fontread.sheets(glyphs, [c for c in sorted(glyphs) if glyphs[c].any()], table, args.draw, args.pairs, "pairs")
            print(f"{len(pages)} sheets of glyphs beside their readings in {args.pairs}", file=sys.stderr)
        twice = fontread.doubles(table)
        if args.check:
            fixed = [c for c in sorted(fixes) if fixes[c] and c in glyphs]
            pages = fontread.sheets(glyphs, fixed, table, args.draw, args.check, "corrections")
            both = [c for ch in twice for c in twice[ch]]
            pages += fontread.sheets(glyphs, both, table, args.draw, args.check, "twice")
            print(f"{len(fixed)} corrections and {len(both)} glyphs given a character another has, on {len(pages)} sheets "
                  f"in {args.check}", file=sys.stderr)
        if args.tbl:
            order = "big" if args.big_endian or lead is not None else "little"
            key = lambda code: code.to_bytes(1 if lead is not None and code < lead else args.unit, order).hex().upper()  # noqa: E731
            args.tbl.write_text("".join(f"{key(c)}={table[c]}\n" for c in sorted(table)), encoding="utf-8")
        inked = sum(1 for g in glyphs.values() if g.any())
        print(json.dumps({"glyphs": len(glyphs), "inked": inked, "read": len(table), "by_the_second_reader": len(readings),
                          "rows": len(rows), "corrections": len(fixes), "characters_given_twice": len(twice),
                          "not_read": inked - len([c for c in table if c in glyphs])}))
        return 0
    if args.cmd == "unread":
        from . import glyphs as gl
        from . import unread
        logs = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in args.logs)
        pictures = unread.logged(logs)
        text = args.dictionary.read_text(encoding="utf-8") if args.dictionary.is_file() else ""
        added = []
        if args.add or args.row:
            try:
                whole = unread.row_readings(unread.rows(logs), {int(a.partition("=")[0]): a.partition("=")[2] for a in args.row})
                for mark, character in whole.items():
                    if gl.read_dictionary(text).get(mark, character) != character:
                        raise ValueError(f"{mark[:4]} is {gl.read_dictionary(text)[mark]} in the dictionary, and a row reads it as {character}")
                before = gl.read_dictionary(text)
                text = unread.add(text, {**whole, **dict(a.partition("=")[::2] for a in args.add)}, list(pictures))
                added = [mark for mark, character in gl.read_dictionary(text).items() if before.get(mark) != character]
            except ValueError as e:
                print(e, file=sys.stderr)
                return 1
            args.dictionary.write_text(text, encoding="utf-8")
        known = gl.read_dictionary(text)
        if added and args.second and args.second.is_file():
            # What was just read, beside the second reader: where the two differ, read the row again.
            theirs = dict(line.split("\t", 1) for line in args.second.read_text(encoding="utf-8").splitlines() if "\t" in line)
            both = [mark for mark in added if mark in theirs and mark in known]
            differ = [mark for mark in both if known[mark] not in theirs[mark].split(" ")]
            for mark in differ:
                print(f"{mark} {known[mark]}: the second reader says {theirs[mark]}")
            print(f"{len(both)} readings added that the second reader has read: {len(both) - len(differ)} the same, "
                  f"{len(differ)} that differ", file=sys.stderr)
        todo = {mark: picture for mark, picture in pictures.items() if mark not in known}
        left = len(todo)
        if args.most > 0:
            todo = dict(list(todo.items())[:args.most])
        font = unread.font_of(args.font, max((p.height for p in pictures.values()), default=1))
        lines = unread.rows_to_read(unread.rows(logs), todo, max(1, args.each))
        readings = {}
        if args.second:
            if args.second.is_file():
                readings = dict(line.split("\t", 1) for line in args.second.read_text(encoding="utf-8").splitlines() if "\t" in line)
            lacking = {mark: picture for mark, picture in todo.items() if mark not in readings}
            if lacking:
                try:
                    alone = unread.second(lacking)
                    in_rows = unread.second_rows([row for row in lines.values() if any(m in lacking for m in row)], pictures, font=font)
                    readings.update({mark: alone[mark] + "".join(" " + c for c in in_rows.get(mark, [])) for mark in lacking})
                except RuntimeError as e:
                    print(e, file=sys.stderr)
                args.second.write_text("".join(f"{k}\t{v}\n" for k, v in sorted(readings.items())), encoding="utf-8")
        for mark, picture in todo.items():
            print(f"{mark} {picture.width}x{picture.height} {readings.get(mark, '')}".rstrip())
        for character, marks in gl.twice(known).items():
            print(f"{character} is given to {len(marks)} pictures in the dictionary: {' '.join(marks)}", file=sys.stderr)
        if args.sheet and todo:
            unread.sheet(todo, args.columns, names=pictures).save(args.sheet)
            if lines:
                unread.row_sheet(lines, pictures, todo, font=font).save(args.sheet.with_name(args.sheet.stem + "-rows" + args.sheet.suffix))
        print(f"{len(pictures)} pictures logged, {len(pictures) - left} in the dictionary, {left} to read"
              + (f": {args.sheet}" if args.sheet and todo else ""), file=sys.stderr)
        return 0
    if args.cmd == "sorted":
        from . import glyphs
        lines = [line.strip() for line in args.readings.read_text(encoding="utf-8").splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        rows = glyphs.in_order([None if line[0] == "?" else line[0] for line in lines], args.order)
        counts = {"ok": 0, "breaks": 0, "unread": 0}
        for row in rows:
            counts[row["status"]] += 1
            if row["status"] != "ok" or args.all:
                must = f" must be {row['must_be']}" if "must_be" in row else ""
                allowed = f" one of {row['allowed']}: {row['candidates']}" if "candidates" in row and not must else ""
                print(f"{row['glyph']:5d} {row['reading'] or '?'} {row['status']}{must}{allowed}")
        pinned = sum(1 for row in rows if "must_be" in row)
        print(f"{len(rows)} glyphs: {counts['ok']} in order, {counts['breaks']} that break it, {counts['unread']} unread; "
              f"{pinned} of those the order settles by itself", file=sys.stderr)
        return 0
    if args.cmd == "nitro":
        from . import nitro
        try:
            card = nitro.card_bytes(args.card)
            chosen = nitro.select(nitro.files(card), args.only)
        except (OSError, zipfile.BadZipFile, nitro.NitroError) as e:
            print(f"nitro: {e}", file=sys.stderr)
            return 1
        for f in chosen:
            data = card[f.offset:f.offset + f.size]
            packed = nitro.packing(data)
            body = nitro.unpack(data) if packed else data
            row = {"path": f.path, "id": f.id, "offset": f.offset, "size": f.size,
                   **({"packed": packed, "unpacked": len(body)} if packed else {})}
            found = nitro.streams(data) if args.streams and not packed else []
            if args.streams:
                row["streams"] = len(found)
                row["streams_unpacked"] = sum(n for _, _, n in found)
            print(json.dumps(row, ensure_ascii=False))
            if args.extract:
                out = args.extract / f.path
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(body if args.unpack else data)
                if args.unpack:
                    for at, _, _ in found:
                        (args.extract / f"{f.path}@{at:X}.bin").write_bytes(nitro.unpack(data, at))
        return 0
    if args.cmd == "rvz":
        from . import rvz
        try:
            disc = rvz.open_disc(args.disc)
            chosen = rvz.select(rvz.files(disc), args.only)
            for f in chosen:
                print(json.dumps({"path": f.path, "offset": f.offset, "size": f.size,
                                  **({"partition": f.partition} if f.partition is not None else {})}, ensure_ascii=False))
                if args.extract:
                    out = args.extract / f.path
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(rvz.content(disc, f))
        except (OSError, rvz.RvzError) as e:
            print(f"rvz: {e}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "placements":
        from . import placements, rvz
        try:
            disc = rvz.open_disc(args.disc)
            found = {f.path: f for f in rvz.files(disc)}
            if args.archive not in found:
                print(f"placements: no file {args.archive} on the disc", file=sys.stderr)
                return 1
            entries = placements.from_archive(rvz.content(disc, found[args.archive]))
        except (OSError, rvz.RvzError, placements.PlacementError) as e:
            print(f"placements: {e}", file=sys.stderr)
            return 1
        for e in placements.select(entries, args.name, args.chunk):
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if args.cmd == "route":
        from . import placements, route, rvz
        try:
            disc = rvz.open_disc(args.disc)
            found = {f.path: f for f in rvz.files(disc)}
            if args.archive not in found:
                print(f"route: no file {args.archive} on the disc", file=sys.stderr)
                return 1
            data = rvz.content(disc, found[args.archive])
            mesh = route.from_archive(data)
            places = [{"name": "", "x": x, "y": y, "z": z} for x, y, z in args.to]
            if args.name:
                places += placements.select(placements.from_archive(data), args.name, args.chunk)
        except (OSError, rvz.RvzError, placements.PlacementError, route.RouteError) as e:
            print(f"route: {e}", file=sys.stderr)
            return 1
        if not places:
            print("route: give --to or --name", file=sys.stderr)
            return 1
        for row in route.targets(mesh, tuple(args.frm), places, low=args.low, up=args.up, down=args.down,
                                 slope=args.slope, cell=args.cell, skip=args.skip, reach=args.reach):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if args.cmd == "disc-text":
        from . import disctext
        from . import tbl as tables
        found = disctext.scan(args.disc, tables.load(args.table), args.least, args.out.parent)
        args.out.write_text("".join(f"{at:X}\t{text}\n" for at, text in found), encoding="utf-8")
        print(f"{len(found)} stretches, {sum(len(text) for _, text in found)} characters, written to {args.out}", file=sys.stderr)
        return 0
    if args.cmd == "identify":
        status = 0
        for img in args.images:
            try:
                print(json.dumps({"image": img.name, **identify(img, args.scratch).as_dict()}, ensure_ascii=False))
            except (DiscError, OSError, ValueError) as e:
                print(json.dumps({"image": img.name, "error": str(e)}), file=sys.stderr)
                status = 1
        return status
    if args.cmd == "scan":
        from .scan import scan_disc
        status = 0
        for img in args.images:
            try:
                rel = identify(img, args.scratch).as_dict()
                res = scan_disc(img, args.scratch).as_dict()
                if not args.samples:
                    res.pop("sample")
                print(json.dumps({"image": img.name, **rel, **res}, ensure_ascii=False))
            except (DiscError, OSError, ValueError) as e:
                print(json.dumps({"image": img.name, "error": str(e)}), file=sys.stderr)
                status = 1
        return status
    if args.cmd == "draft":
        from .draft import draft, summary
        results = [json.loads(l) for l in args.results.read_text(encoding="utf-8").splitlines() if l.strip()]
        titles = json.loads(args.titles.read_text(encoding="utf-8")) if args.titles else None
        print(summary(draft(results, args.root, titles)))
        return 0
    if args.cmd == "hooks":
        from .hooks import merge, summary
        print(summary(merge(args.root, args.reports, args.min_lines, args.min_rate)))
        return 0
    if args.cmd == "where":
        from .where import report, standard, where

        def raw(p: Path) -> bytes:
            return snapfile.read(p)
        metas = [json.loads(Path(str(p).replace(".ram.gz", ".json")).read_text(encoding="utf-8")) for p in args.snapshots]
        bases = {int(m["base_address"], 16) if m.get("base_address") else RAM_BASE.get(m.get("platform"), 0) for m in metas}
        if len(bases) != 1:
            print("the snapshots start at different addresses", file=sys.stderr)
            return 1
        snaps = [(raw(p), m.get("ocr") or []) for p, m in zip(args.snapshots, metas)]
        base = bases.pop()
        plain = None if args.pattern else standard(snaps, base)
        if plain:
            print(plain)
            return 0
        buffers, mapping = where(snaps, [raw(p) for p in args.absent])
        print(report(buffers, mapping, base, len(snaps), args.table))
        # A snapshot with its video memory and frame can be read without any lines, where text is tiles.
        for p, (ram, _) in list(zip(args.snapshots, snaps))[:args.frames]:
            stem = p.name.split(".")[0]
            video, frame = p.with_name(stem + ".vram.gz"), p.with_name(stem + ".screen.png")
            if video.is_file() and frame.is_file():
                import numpy as np
                from PIL import Image
                from .where import tile_report
                rgb = np.asarray(Image.open(frame).convert("RGB")).astype(np.int64)
                if rgb.shape[1] > 256:                                  # a doubled frame
                    rgb = rgb[::rgb.shape[1] // 256, ::rgb.shape[1] // 256]
                packed = rgb[:, :, 0] << 16 | rgb[:, :, 1] << 8 | rgb[:, :, 2]
                print(f"\n{stem[:8]}, from its frame and video memory:")
                print(tile_report(ram, raw(video), packed, base, args.bpp, int(args.mask, 16)))
        return 0
    if args.cmd == "locate":
        from .locate import find_standard, locate, propose
        snaps, bases = [], []
        for p in args.snapshots:
            meta = json.loads(Path(str(p).replace(".ram.gz", ".json")).read_text(encoding="utf-8"))
            snaps.append((snapfile.read(p), meta["ocr"]))
            bases.append(int(meta["base_address"], 16) if meta.get("base_address") else RAM_BASE.get(meta.get("platform"), 0))
        standard = find_standard(snaps)
        for enc, hits in standard.items():
            print(f"== {enc}: {sum(len(h['segment']) for h in hits)} characters of the lines found as they are, at")
            for n, p in enumerate(args.snapshots):
                mine = sorted((h for h in hits if h["snapshot"] == n), key=lambda h: h["offset"])
                print(f"   {p.name.split('.')[0][:8]}: " + (" ".join(
                    f"0x{bases[n] + h['offset']:08X}" + (f"({h['segment']})" if args.show_hits else f"+{len(h['segment'])}")
                    for h in mine) or "nothing"))
        if standard:
            charset, hits = next(iter(standard.items()))
            got = propose(snaps, hits, charset, bases[0]) if len(set(bases)) == 1 else None
            if got:
                rows = got["message"].get("rows")
                print("The lines stand in one fixed buffer. As a layout's `message`"
                      + (f" ({got['rows_seen']} of its {rows['count']} rows were seen with text; the rest were empty up to"
                         " where other data begins)" if rows and rows["count"] > got["rows_seen"] else "")
                      + (" (max_bytes is a guess: twice the longest line seen)" if not rows else "") + ":")
                print("   " + json.dumps(got["message"], ensure_ascii=False))
                print("   read back against OCR: " + ", ".join(
                    f"{p.name.split('.')[0][:8]} " + ("empty, the screen's text is elsewhere" if a is None else f"{a:.0%}")
                    + (f" (and {more} characters the screen didn't show: still typing?)" if more > 3 else "")
                    for p, a, more in zip(args.snapshots, got["agreement"], got["longer"])))
            else:
                print("No place was seen to change from one line to another, so the window's buffer can't be told from "
                      "script or backlog copies yet: snapshots of more lines will settle it.")
        if standard and not args.pattern:
            print("A standard encoding matched, so the search by pattern (for a game's own table) was skipped; --pattern runs it.")
            return 0
        for r in locate(snaps):
            print(f"== {r.encoding}: {len(r.hits)} matches, {len(r.mapping)} codes, {len(r.conflicts)} conflicts")
            print("   kana layout (code - gojuon position -> count):", r.gojuon_offsets())
            print("   mapping:", " ".join(f"{c:04X}={ch}" for c, ch in sorted(r.mapping.items())))
            for c in r.conflicts[:10]:
                print("   conflict", c)
            if args.show_hits:
                for h in r.hits[:30]:
                    print(f"   @{h['offset']:#08x} {h['segment']}")
        return 0
    if args.cmd == "vram":
        from PIL import Image
        from .vram import extract_vram, views
        load = snapfile.read
        got = extract_vram(load(args.state), load(args.ram) if args.ram else None)
        args.out.with_suffix(".vram").write_bytes(got.vram)
        for name, arr in views(got.vram).items():
            Image.fromarray(arr).save(f"{args.out}-{name}.png")
        print(f"video memory at offset {got.vram_offset:#x}" + (f", RAM at {got.ram_offset:#x}" if got.ram_offset is not None else ""))
        return 0
    if args.cmd == "font":
        from . import font
        load = snapfile.read
        vram, first = load(args.vram), int(args.first, 16)
        if args.grid:
            x, y, w, h, columns, rows = (int(v) for v in args.grid.split(","))
            cells = font.grid(vram, x, y, w, h, columns, rows)
            glyphs = {first + n: g for n, g in enumerate(cells)}
        else:
            mask, base, columns = int(args.mask, 16), int(args.base, 16) if args.base else None, 16
            if base is None:
                if not (args.cells and args.at):
                    print("give --base, or --cells and --at to find it", file=sys.stderr)
                    return 1
                meta_path = args.cells.with_name(args.cells.name.split(".")[0] + ".json")
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
                start = args.ram_base or meta.get("base_address")
                start = int(start, 16) if start else RAM_BASE.get(meta.get("platform"), 0)
                ram, at = load(args.cells), int(args.at, 16) - start
                used = [int.from_bytes(ram[at + 2 * i:at + 2 * i + 2], "little") & mask for i in range(args.count)]
                found = font.find_base(vram, used, args.bpp, args.packed)
                if not found:
                    print("no base fits those cells: wrong --bpp, or the buffer doesn't hold tile numbers", file=sys.stderr)
                    return 1
                ties = [b for b, score in found if score == found[0][1]]
                if len(ties) > 1:
                    print("these bases fit the buffer's cells equally well: " + ", ".join(f"0x{b:X}" for b in ties)
                          + ". Try each with --base; more cells (--count) with different characters may settle it",
                          file=sys.stderr)
                    return 1
                base = found[0][0]
                print(f"tiles at 0x{base:X} ({found[0][1]} of the buffer's tiles are different pictures there"
                      + (f"; next best 0x{found[1][0]:X} with {found[1][1]}" if len(found) > 1 else "") + ")")
            last = int(args.last, 16) if args.last else mask
            cut = font.tiles(vram, base, args.bpp, last + 1 + (args.tall or 0), args.packed)
            if args.tall:
                glyphs = {t: g for t, g in font.pair(cut[first:], args.tall).items()}
                glyphs = {first + t: g for t, g in glyphs.items() if first + t <= last}
            else:
                glyphs = {t: cut[t] for t in range(first, min(last + 1, len(cut)))}
        blanks = {code for code, g in glyphs.items() if font.blank(g)}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        font.sheet(glyphs, columns).save(args.out)
        print(f"{len(glyphs)} glyphs, {len(blanks)} blank -> {args.out} (the game's font: don't commit it)")
        if args.tbl:
            known = font.known_pairs(args.known.read_text(encoding="utf-8"), args.unit, args.big_endian,
                                     None if args.grid else int(args.mask, 16)) if args.known else {}
            attributes = [int(a, 16) for a in args.attributes.split(",")]
            args.tbl.write_text(font.skeleton(sorted(glyphs), args.unit, known, blanks, attributes, args.big_endian), encoding="utf-8")
            print(f"{args.tbl}: {len(glyphs) - len(blanks)} codes to read, {sum(1 for c in glyphs if c in known)} already known")
        return 0
    if args.cmd == "replay":
        import gzip
        import numpy as np
        from PIL import Image
        from .replay.libretro import Core
        args.out.mkdir(parents=True, exist_ok=True)
        core = Core(args.core, args.system or args.out / "system", dict(args.option))
        core.load_game(args.game)
        state = b""
        if args.state:
            raw = args.state.read_bytes()
            state = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
            core.load_state(state)
        if args.steps:
            from .replay import steps
            base = RAM_BASE[args.platform]
            watch = {a: int(a, 16) - base for a in args.watch.split(",") if a}
            for line in steps.run(core, steps.parse(args.steps), args.out, watch):
                print(line)
            return 0
        core.run(args.wait)
        if args.press:
            core.run(3, hold=set(args.press.split(",")))
        rams, frames = [], []
        for f in range(args.frames):
            core.run(1)
            rams.append(np.frombuffer(core.ram(), dtype=np.uint8).copy())
            frames.append(core.frame_rgb())
            if f % 10 == 0:
                Image.fromarray(frames[-1]).save(args.out / f"frame-{f:04d}.png")
        np.save(args.out / "ram.npy", np.stack(rams))
        np.save(args.out / "frames.npy", np.stack(frames))
        print(f"recorded {args.frames} frames" + (f" (state {len(state)} bytes, core expects {core.state_size()})" if state else ""))
        return 0
    if args.cmd in ("contrast", "find"):
        from .contrast import contrast, find_text
        from .layout import Ram
        base = RAM_BASE[args.platform]
        if args.cmd == "find":
            absent = [Ram.load(p, base).data for p in args.absent]
            for p in args.snapshots:
                for charset, at in find_text(Ram.load(p, base).data, args.text, absent).items():
                    print(p.name, charset, " ".join(f"0x{base + o:08X}" for o in at))
            return 0
        hits = contrast([Ram.load(p, base).data for p in args.same], [Ram.load(p, base).data for p in args.other])
        for offset, value, others in hits[:args.limit]:
            print(f"0x{base + offset:08X} {value}, otherwise {' '.join(map(str, others[:8]))}")
        print(f"{len(hits)} byte(s)")
        return 0
    if args.cmd == "layout":
        from . import layout, tbl
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        meta_path = args.ram.with_name(args.ram.name.split(".")[0] + ".json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        lays = layout.layouts_for(entry, args.version or meta.get("exe_sha1"), meta.get("id"))
        if not lays:
            print("no layout for that version", file=sys.stderr)
            return 1
        base = args.base or meta.get("base_address")
        base = int(base, 16) if base else layout.RAM_BASE.get(entry["platform"])
        if base is None:
            print(f"give --base for {entry['platform']}", file=sys.stderr)
            return 1
        table = tbl.load([args.root / t for t in entry["tables"]]) if entry.get("tables") else None
        load = lambda paths: tbl.load([args.root / t for t in paths])
        ram = layout.Ram.load(args.ram, base)
        read = layout.read_layout(layout.live(lays, ram), entry, ram, table, load)
        print(json.dumps(read, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "check":
        from .check import check, page
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        try:
            result = check(entry, args.snapshots, args.root, int(args.base, 16) if args.base else None, args.version)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        for name, n in result.counts().items():
            print(f"{n:5d}  {name}")
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "report.html").write_text(page(result, entry["id"]), encoding="utf-8")
            print(f"{args.out / 'report.html'} (game text and frames: don't commit it)")
        return 0
    if args.cmd == "bundle":
        from .bundle import BundleError, bundle
        try:
            text = bundle(args.script)
        except (BundleError, OSError) as e:
            print(e, file=sys.stderr)
            return 1
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.cmd == "compare":
        from . import reader as rd
        from .compare import compare
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        try:
            result = compare(args.script, entry, args.snapshots, args.entry.resolve().parents[2],
                             int(args.base, 16) if args.base else None, args.version, args.frames)
        except (rd.ReaderUnavailable, ValueError, OSError) as e:
            print(e, file=sys.stderr)
            return 1
        for name, n in result.counts().items():
            print(f"{n:5d}  {name}")
        for row in result.rows:
            if row.verdict in ("different", "the script didn't load"):
                print(f"{row.name[:12]}: {row.error or ', '.join(row.differing())}", file=sys.stderr)
                if args.show and row.error is None:
                    for key in row.differing():
                        print(f"  layout {key}: {row.layout[key]!r}\n  script {key}: {row.script[key]!r}", file=sys.stderr)
        return 0 if result.agrees else 1
    if args.cmd == "regress":
        from . import reader as rd
        from .regress import markdown, regress, summary
        try:
            report = regress(args.scratch, args.games, args.catalog, tuple(args.accept), args.frames)
        except rd.ReaderUnavailable as e:
            print(e, file=sys.stderr)
            return 2
        import datetime as dt
        print(markdown(report, dt.date.today().isoformat()), end="")
        if report.failed and args.summary:
            args.summary.write_text(summary(report) + "\n", encoding="utf-8")
        return 1 if report.failed else 0

    if args.cmd == "run":
        from . import reader as rd
        from .run import STORY_CHANNELS, run
        try:
            readings = run(args.script, args.snapshots, platform=args.platform,
                           base=int(args.base, 16) if args.base else None, frames=args.frames, sequence=args.sequence,
                           dump=args.dump, rom=args.rom.read_bytes() if args.rom else None)
        except (rd.ReaderUnavailable, ValueError, OSError) as e:        # a BundleError is a ValueError
            print(e, file=sys.stderr)
            return 1
        status = 0
        if args.dump:
            for reading in readings:
                if reading.error is not None:
                    print(f"{reading.name}: {reading.error}", file=sys.stderr)
                    status = 1
                texts = [e["text"] for e in reading.texts()]
                unread = sum(t.count("\ufffd") for t in texts)
                print(f"{reading.name}: {len(texts)} texts, {sum(len(t) for t in texts)} characters, "
                      f"{unread} without a reading", file=sys.stderr)
                if args.unconfirmed:
                    sure = set(args.unconfirmed.read_text(encoding="utf-8")) if args.unconfirmed.is_file() else set()
                    doubt = lambda ch: "\u4e00" <= ch <= "\u9fff" and ch not in sure      # noqa: E731
                    left = set()
                    for text in sorted({t for t in texts if any(doubt(ch) for ch in t)}, key=len):
                        left |= {ch for ch in text if doubt(ch)}
                        print("".join(f"【{ch}】" if doubt(ch) else ch for ch in text).replace("\n", " / "))
                    print(f"{reading.name}: {len(left)} kanji not yet confirmed in a sentence", file=sys.stderr)
                elif args.containing:
                    for ch in dict.fromkeys(args.containing):
                        for text in sorted((t for t in texts if ch in t), key=len)[:2]:
                            print(json.dumps({"snapshot": reading.name, "character": ch, "text": text}, ensure_ascii=False))
                else:
                    for event in reading.texts():
                        print(json.dumps({"snapshot": reading.name, **event}, ensure_ascii=False))
            return status
        for reading in readings:
            if reading.error is not None:
                print(f"{reading.name}: {reading.error}", file=sys.stderr)
                status = 1
            for kind, event in reading.events:
                if args.story and kind == "text" and event["channel"] not in STORY_CHANNELS:
                    continue
                if kind != "log" or args.log:
                    print(json.dumps({"snapshot": reading.name, "kind": kind, **event}, ensure_ascii=False))
        return status
    if args.cmd == "propose":
        from collections import defaultdict
        import numpy as np
        from PIL import Image
        from . import propose as pr
        from .check import check, sidecars
        load = snapfile.read
        rams, found, base, platform, looked = [], [], None, args.platform, set()
        for n, path in enumerate(args.snapshots):
            meta, frame = sidecars(path)
            video = path.with_name(path.name.split(".")[0] + ".vram.gz")
            rams.append(load(path))
            platform = meta.get("platform", platform)
            base = int(meta["base_address"], 16) if meta.get("base_address") else RAM_BASE[platform]
            if not (frame and video.is_file()):
                continue
            rgb = np.asarray(Image.open(frame).convert("RGB")).astype(np.int64)
            if rgb.min() == rgb.max():                  # a blank frame (a replay's first snap) shows nothing either way
                continue
            looked.add(n)
            if rgb.shape[1] > 256:
                rgb = rgb[::rgb.shape[1] // 256, ::rgb.shape[1] // 256]
            found += pr.windows(rams[-1], load(video), rgb[:, :, 0] << 16 | rgb[:, :, 1] << 8 | rgb[:, :, 2], n,
                                args.bpp, int(args.mask, 16))
        kinds = defaultdict(list)
        for w in found:
            kinds[w.kind].append(w)
        if not kinds:
            print("no window of tiles found: the snapshots need their video memory and frame, and two rows of text on screen",
                  file=sys.stderr)
            return 1
        entry = {"id": "DRAFT", "platform": platform, "encoding": "table", "tables": args.table, "unit": args.unit}
        scored = []
        for kind, ws in kinds.items():
            layouts, notes = pr.draft(ws, base, args.table, rams, [w for w in found if w.kind != kind], looked)
            counts = check({**entry, "layouts": layouts}, args.snapshots, args.root, base).counts() if args.table else {}
            agree = next((v for k, v in counts.items() if k.startswith("of those, agreeing")), 0)
            scored.append((agree, sum(w.cells for w in ws), kind, ws, layouts, notes, counts))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        print("Windows of tiles found (cells wide x rows of text):")
        for agree, cells, kind, ws, layouts, notes, counts in scored:
            where_at = sorted({f"0x{base + w.offset:06X}" for w in ws})
            print(f"  {kind[0]}x{kind[1]}" + (" two-tile characters" if kind[2] else "") + f": in {len({w.snapshot for w in ws})} of "
                  f"{len(rams)} snapshot(s), at {', '.join(where_at)}; {cells} cells seen"
                  + (f"; agrees with the reader's lines in {agree}" if counts else ""))
        pick = next((t for t in scored if f"{t[2][0]}x{t[2][1]}" == args.kind), scored[0])
        _, _, kind, ws, layouts, notes, counts = pick
        print(f"\nDraft for {kind[0]}x{kind[1]}:")
        for note in notes:
            print(f"  - {note}")
        text = json.dumps(layouts, ensure_ascii=False, indent=1)
        print(text if not args.out else f"  {len(layouts)} layout(s) -> {args.out}")
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        if counts:
            print("Read back with the draft:")
            for name, n in counts.items():
                print(f"{n:5d}  {name}")
        return 0
    if args.cmd == "validate":
        problems = catalog.validate(args.root)
        for pr in problems:
            print(pr, file=sys.stderr)
        n = sum(1 for _ in catalog.entries(args.root))
        print(f"{n} entries, {len(catalog.scripts(args.root))} scripts, {len(problems)} problem(s)")
        return 1 if problems else 0
    if args.cmd == "index":
        if args.check:
            stale = catalog.stale(args.root)
            if stale:
                print(f"out of date: {', '.join(stale)}: run `yomikata index`", file=sys.stderr)
                return 1
            return 0
        print("index.json and scripts/ updated" if catalog.write_index(args.root) else "index.json and scripts/ unchanged")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
