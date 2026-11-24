/* SPDX-FileCopyrightText: Yomikata contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The Yomikata reader: runs one game's script (docs/script-api.md) against memory the host reads for it,
 * and hands back what the script says is on screen.
 *
 * One yk_reader belongs to one running game. Its functions are called from one thread at a time (the
 * emulator's, between two frames). The host's functions are called from inside those calls, on the same
 * thread, and must not call back into the reader.
 */
#ifndef YOMIKATA_READER_H
#define YOMIKATA_READER_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  if defined(YK_BUILD)
#    define YK_API __declspec(dllexport)
#  else
#    define YK_API __declspec(dllimport)
#  endif
#else
#  define YK_API __attribute__((visibility("default")))
#endif

/* Changes when this header changes in a way an older host can't follow. */
#define YK_ABI 1

typedef struct yk_reader yk_reader;

/* What comes out. Each is one line of JSON, UTF-8, valid only during the call.
 *   YK_TEXT    {"seq","frame","channel","speaker","text","ruby":[[start,length,"reading"]…],"id","meta":{…},
 *              "window"?}
 *              channel is "dialogue", "choice" or "interface". Interface text (menus, item names, status
 *              screens) is for looking up on the picture only: a host keeps it out of its reader, its
 *              backlog and its reading counts, and drops a channel it doesn't know.
 *   YK_LAYOUT  {"window","seq","screen"?:1,"space":[w,h],"box"?:[x,y,w,h],"glyphs":[[x,y,w,h]|null…]}
 *              or {"window","seq","closed":true}
 *   YK_CURSOR  {"index":n,"horizontal":bool} or {"closed":true}
 *   YK_LOG     {"level":"info"|"warning"|"error","message"} */
enum { YK_TEXT = 1, YK_LAYOUT = 2, YK_CURSOR = 3, YK_LOG = 4 };

typedef struct yk_host {
    void* user; /* handed back as the first argument of each function below */

    /* Copies up to `size` bytes of the game's memory at `address` (the game's own address, as
     * docs/script-api.md lists them per platform) into `out`. Returns how many bytes it copied: fewer than
     * asked, or 0, where memory isn't there. Bytes as they lie in memory; nothing is swapped. */
    size_t (*read)(void* user, uint64_t address, void* out, size_t size);

    /* A named region ("vram"): its size in bytes, or -1 when the host hasn't got it. May be NULL. */
    int64_t (*region_size)(void* user, const char* name);
    /* Copies up to `size` bytes of a region from `offset`. Returns how many. May be NULL. */
    size_t (*region_read)(void* user, const char* name, uint64_t offset, void* out, size_t size);

    /* A script's data file (reader.data(name): the file <id>.<name> beside the script). Copies up to
     * `capacity` bytes into `out` and returns the file's whole size, or -1 when there is none. It is first
     * called with a capacity of 0 to learn the size. May be NULL. */
    int64_t (*data)(void* user, const char* name, void* out, size_t capacity);

    /* One event (YK_TEXT …). */
    void (*event)(void* user, int kind, const char* json, size_t length);
} yk_host;

typedef struct yk_options {
    uint32_t abi;         /* YK_ABI */
    const char* platform; /* the catalog's name: "ps1", "gc", "nds" … (a host may know GameCube as "ngc":
                             the catalog says "gc"). Decides a layout's default space and whether there is
                             a lower screen. May be NULL. */
    const char* game_id;  /* reader.game_id, as in the script's file name */
    const char* build_id; /* reader.build_id, or NULL when the host can't hash the game */
    int has_base;         /* whether `base` is known (PSP) */
    uint64_t base;        /* reader.base: where the game's main program was loaded */
    /* Limits; 0 takes the floor of docs/script-api.md. */
    uint64_t load_instructions; /* Lua instructions while the script loads (floor: 100 million) */
    uint64_t call_instructions; /* … and in one call of one of its functions (floor: 2 million) */
    uint64_t memory;            /* bytes the script may hold (floor: 32 MB) */
} yk_options;

YK_API const char* yk_version(void);

/* A reader for one game. `host` and `options` are copied. NULL when `host` lacks read or event, the abi
 * differs, or memory ran out. */
YK_API yk_reader* yk_open(const yk_host* host, const yk_options* options);
YK_API void yk_close(yk_reader* reader);

/* Loads a script, in place of the one loaded before. `name` is for messages (the file's name). Returns 1
 * when the script loaded; 0 when it failed (it is then not loaded, and yk_error says why): a syntax error,
 * its own "another build" error, a region it needs that the host hasn't got, a limit. */
YK_API int yk_load(yk_reader* reader, const char* name, const char* source, size_t length);
YK_API void yk_unload(yk_reader* reader);
/* The last error, or "". Valid until the next call on this reader. */
YK_API const char* yk_error(yk_reader* reader);

/* Call between two frames of the game, with how many frames the game has run. Not every frame need be
 * reported, but the count must not go back except after yk_state_loaded. */
YK_API void yk_frame(yk_reader* reader, uint64_t frame);
/* Call after a savestate was loaded, before the next yk_frame: a host that has savestates must. */
YK_API void yk_state_loaded(yk_reader* reader);
/* The size in pixels of the picture the game shows (reader.picture). */
YK_API void yk_picture(yk_reader* reader, uint32_t width, uint32_t height);

/* For tools: runs a chunk in the script's state (an empty one when no script is loaded) and returns what
 * it returned, tab-separated, or its error; *ok says which. Valid until the next call on this reader. */
YK_API const char* yk_eval(yk_reader* reader, const char* code, size_t length, int* ok);

/* Reads a script's header lines without running it. Calls `line` once for each "-- reader: <key> <rest>"
 * among the comments the source begins with ("no hooks" and "no layout" are keys of two words). */
YK_API void yk_header(const char* source, size_t length,
                      void (*line)(void* user, const char* key, const char* rest), void* user);

#ifdef __cplusplus
}
#endif

#endif
