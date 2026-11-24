# The reader runtime

One small library that runs a game's script (`docs/script-api.md`): Lua 5.4, the `reader`, `mem` and `text`
functions, the limits, and nothing of any emulator. A host gives it a way to read the game's memory and calls it
between frames; it hands back what the script says is on screen, as lines of JSON. The interface is the C header
`include/yomikata_reader.h`, so an app in any language can embed it: Genkan through JNI, the tools here through
Python's ctypes (`tools/src/yomikata/reader.py`).

- `src/lua_api.cpp`, `src/text_codec.*` and `src/sjis_table.inc` come from
  [Furoshiki](https://github.com/sommepo/furoshiki)'s reader (`shared/reader/core`, GPL-2.0-or-later), so emits,
  ids, `reader.changed` and the decoders behave as they do there. Each file says what was changed.
  `src/engine.*` is a smaller engine written for this, after Furoshiki's.
- `third_party/lua` is Lua 5.4.8, unchanged, compiled as C++ inside the library.
- The library is GPL-3.0-or-later as a whole; see `NOTICE.md`.

## Building

CMake 3.20 or later and a C++20 compiler (GCC 11, Clang 14, or later).

```sh
cmake -S runtime -B runtime/build
cmake --build runtime/build
```

That leaves `runtime/build/libyomikata_reader.so`, which the tools find there by themselves
(or through `YOMIKATA_READER_LIB`). `-DYOMIKATA_READER_SHARED=OFF` builds a static library instead, for an app
that links it into a library of its own.

Only the `yk_` functions show from the shared library: Lua's names are inside it, so an emulator that has a
Lua of its own in the same process isn't disturbed.

### Android (arm64-v8a)

With the NDK's own toolchain file (checked with NDK r27c):

```sh
cmake -S runtime -B runtime/build-android \
  -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static
cmake --build runtime/build-android
```

- `c++_static`: the C++ library is inside, hidden (`-Wl,--exclude-libs,ALL`), so the app's other libraries may
  use any other. The library needs only `libc`, `libm` and `libdl`.
- It is linked for 16 KB pages (`-Wl,-z,max-page-size=16384`).
- About 700 KB once stripped (Gradle strips it when packaging).
- From Gradle the same thing is `externalNativeBuild { cmake { path "…/runtime/CMakeLists.txt" } }` with
  `arguments "-DANDROID_STL=c++_static"`, or `add_subdirectory` from the app's own CMake file with
  `YOMIKATA_READER_SHARED` off, linking `yomikata_reader` into the app's JNI library.

## Using it

```c
yk_host host = { .user = app, .read = read_memory, .event = on_event };   /* regions and data: optional */
yk_options options = { .abi = YK_ABI, .platform = "gc", .game_id = "GXXJ01" };
yk_reader* reader = yk_open(&host, &options);
if (!yk_load(reader, "GXXJ01.lua", source, length))
    show(yk_error(reader));              /* "this script is for another build of the game", say */
/* between two frames of the game: */
yk_frame(reader, frames_run);
/* after loading a savestate, before the next frame: */
yk_state_loaded(reader);
yk_close(reader);
```

What a host must do:

- **Read by the game's own addresses** (`docs/script-api.md`, "Address spaces"), giving bytes as they lie in
  memory. A read that runs off the end of memory returns what there is.
- **Call `yk_frame` between frames**, on the thread that runs the game, so that every read in one call sees one
  moment. Not every frame need be reported.
- **Call `yk_state_loaded` after a savestate is loaded.** The script then forgets what it believed to be on
  screen, and every layout is closed.
- **Name the platform as the catalog does** (`gc`, where some apps say `ngc`).
- Not call back into the reader from `read`, `event` and the others.

Events arrive through `event` during those calls: `YK_TEXT`, `YK_LAYOUT`, `YK_CURSOR` and `YK_LOG`, each a line
of JSON described in the header. The limits a script runs under are the floor of `docs/script-api.md` unless
the options raise them.

A failure inside the library (memory, a fault in the script's state) never ends the process: the script is
unloaded and `yk_error` says why.

## Tests

`tools/tests/test_reader.py` runs the library through the Python binding on made-up memory: the reads in both
byte orders, regions, emits and layouts, the limits, the switching off of a callback that fails five times,
the read-only rule, and that two runs over the same memory give the same events byte for byte.

```sh
pip install -e "tools[dev]" && pytest tools/tests/test_reader.py
```
