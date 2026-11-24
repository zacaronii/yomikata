// SPDX-FileCopyrightText: Flycast Reader fork contributors (from the PPSSPP and Eden Reader forks)
// SPDX-FileCopyrightText: Yomikata contributors
// SPDX-License-Identifier: GPL-2.0-or-later
//
// From Furoshiki (shared/reader/core/lua_api.cpp). Changed for Yomikata (docs/script-api.md):
// - read-only and memory-only: reader.hook and mem.w8 … w64 are errors; reader.action and reader.progress
//   are checked and left alone; no cpu view, no hook descriptions;
// - added: the big-endian and signed reads, and mem.region(name);
// - the limits are the host's (Engine::GetLimits), not constants; a layout's default space is the
//   platform's (Engine::GetScreens);
// - math.random starts from a fixed seed, so that two runs over the same memory give the same events;
// - no std::format, and a Lua panic is an exception instead of the end of the process.

#include <algorithm>
#include <array>
#include <bit>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include "engine.h"
#include "lua_api.h"
#include "text_codec.h"

// The reader has a Lua of its own, built as C++ (see CMakeLists.txt), so no extern "C". Lua errors
// are therefore C++ exceptions and unwind through the bindings below safely.
#include <lauxlib.h>
#include <lua.h>
#include <lualib.h>

namespace Reader::LuaApi {

namespace {

/// The count hook fires every this many VM instructions; a limit is counted in such steps.
constexpr int BudgetStep = 10000;

constexpr lua_Integer MaxStringRead = 1 << 20;

/// Per-state bookkeeping, shared by the main thread and every coroutine of a state.
struct StateData {
    std::size_t used{};     ///< Bytes currently allocated
    std::intptr_t budget{}; ///< Count-hook steps left for the running callback
    std::size_t max_memory{};      ///< Bytes the script may hold: a runaway allocation is a Lua error
    std::intptr_t load_steps{};    ///< Steps the loading of a script may use
    std::intptr_t callback_steps{}; ///< Steps one callback may use
};

void* LimitedAlloc(void* ud, void* ptr, std::size_t old_size, std::size_t new_size) {
    auto* account = static_cast<StateData*>(ud);
    // For a new block Lua passes the kind of object in old_size, not a size.
    const std::size_t held = ptr ? old_size : 0;
    if (new_size == 0) {
        std::free(ptr);
        account->used -= held;
        return nullptr;
    }
    if (new_size > held && account->used - held + new_size > account->max_memory) {
        return nullptr;
    }
    void* block = std::realloc(ptr, new_size);
    if (block) {
        account->used = account->used - held + new_size;
    }
    return block;
}

StateData& DataOf(lua_State* L) {
    void* ud = nullptr;
    lua_getallocf(L, &ud);
    return *static_cast<StateData*>(ud);
}

std::intptr_t& BudgetLeft(lua_State* L) {
    return DataOf(L).budget;
}

const char EngineKey = 0;

Engine& GetEngine(lua_State* L) {
    lua_rawgetp(L, LUA_REGISTRYINDEX, &EngineKey);
    auto* engine = static_cast<Engine*>(lua_touserdata(L, -1));
    lua_pop(L, 1);
    return *engine;
}

void BudgetHook(lua_State* L, lua_Debug*) {
    std::intptr_t& budget = BudgetLeft(L);
    if (budget > 0 && --budget > 0) {
        return;
    }
    // Spent: every further step fails too, so that a pcall inside the script cannot keep the
    // callback going (see the pcall wrappers in the prelude).
    luaL_error(L, "script callback exceeded its instruction budget (endless loop?)");
}

/// True once the running callback has used up its budget.
int BudgetExhausted(lua_State* L) {
    lua_pushboolean(L, BudgetLeft(L) <= 0);
    return 1;
}

u64 CheckAddress(lua_State* L, int index) {
    // Addresses are 64-bit and Lua's integers are signed 64-bit: the bits are what counts.
    return static_cast<u64>(luaL_checkinteger(L, index));
}

std::span<const u8> CheckBytes(lua_State* L, int index) {
    std::size_t size = 0;
    const char* data = luaL_checklstring(L, index, &size);
    return {reinterpret_cast<const u8*>(data), size};
}

void PushString(lua_State* L, const std::string& s) {
    lua_pushlstring(L, s.data(), s.size());
}

std::string FieldString(lua_State* L, int table, const char* name) {
    std::string out;
    lua_getfield(L, table, name);
    if (lua_type(L, -1) == LUA_TSTRING || lua_type(L, -1) == LUA_TNUMBER) {
        std::size_t size = 0;
        const char* data = lua_tolstring(L, -1, &size);
        out.assign(data, size);
    } else if (!lua_isnil(L, -1)) {
        lua_pop(L, 1);
        luaL_error(L, "emit: field '%s' must be a string", name);
    }
    lua_pop(L, 1);
    return out;
}

// ---- reader ----

int ReaderHook(lua_State* L) {
    return luaL_error(L, "hook: this reader only reads memory; a script here has no hooks");
}

int ReaderOnFrame(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    lua_pushvalue(L, 1);
    GetEngine(L).AddFrameCallback(luaL_ref(L, LUA_REGISTRYINDEX));
    return 0;
}

int ReaderOnStateLoaded(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    lua_pushvalue(L, 1);
    GetEngine(L).AddStateCallback(luaL_ref(L, LUA_REGISTRYINDEX));
    return 0;
}

/// reader.action(label, fn): Furoshiki offers the player something to do in the game, which writes its
/// memory. Here nothing is written: the call is checked, so that such a script loads, and left alone.
int ReaderAction(lua_State* L) {
    std::size_t size = 0;
    const char* label = luaL_checklstring(L, 1, &size);
    luaL_argcheck(L, size > 0 && size <= 200, 1, "a label of 1 to 200 bytes");
    luaL_argcheck(L, std::string_view(label, size).find('\n') == std::string_view::npos, 1,
                  "a label of one line");
    luaL_checktype(L, 2, LUA_TFUNCTION);
    return 0;
}

/// reader.cursor(index [, "horizontal"]): which answer of the choice on screen the game's cursor
/// is on, from 0 (-1 while it is on none yet); reader.cursor(nil) once there is no choice. What
/// touch play turns a tap on an answer into presses of the d-pad with.
int ReaderCursor(lua_State* L) {
    if (lua_isnoneornil(L, 1)) {
        GetEngine(L).SetCursor(std::nullopt);
        return 0;
    }
    const lua_Integer index = luaL_checkinteger(L, 1);
    luaL_argcheck(L, index >= -1 && index < 64, 1, "an answer from 0, or -1 for none");
    const std::string_view way{luaL_optstring(L, 2, "vertical")};
    luaL_argcheck(L, way == "vertical" || way == "horizontal", 2, "\"vertical\" or \"horizontal\"");
    GetEngine(L).SetCursor(Engine::AnswerCursor{static_cast<int>(index), way == "horizontal"});
    return 0;
}

/// reader.progress{title, note, items = {{id, label, group}...}, done = f(id), mark = f(id, on)}:
/// progress the player may set by hand in Furoshiki, by writing memory. Checked and left alone here.
int ReaderProgress(lua_State* L) {
    luaL_checktype(L, 1, LUA_TTABLE);
    lua_getfield(L, 1, "items");
    luaL_argcheck(L, lua_istable(L, -1), 1, "items must be a list");
    lua_getfield(L, 1, "done");
    luaL_argcheck(L, lua_isfunction(L, -1), 1, "done must be a function");
    lua_getfield(L, 1, "mark");
    luaL_argcheck(L, lua_isfunction(L, -1), 1, "mark must be a function");
    lua_pop(L, 3);
    return 0;
}

/// The shortest decimal that reads back as the same number.
std::string NumberText(double value) {
    char buffer[32];
    const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value);
    return std::string(buffer, result.ptr);
}

int ReaderEmit(lua_State* L) {
    luaL_checktype(L, 1, LUA_TTABLE);
    TextEvent event;
    event.text = FieldString(L, 1, "text");
    event.speaker = FieldString(L, 1, "speaker");
    event.channel = FieldString(L, 1, "channel");
    event.source_id = FieldString(L, 1, "id");
    event.window = FieldString(L, 1, "window");

    lua_getfield(L, 1, "ruby");
    if (lua_istable(L, -1)) {
        const lua_Integer count = luaL_len(L, -1);
        for (lua_Integer i = 1; i <= count; ++i) {
            lua_rawgeti(L, -1, i);
            if (!lua_istable(L, -1)) {
                return luaL_error(L, "emit: ruby[%d] must be {start, length, reading}",
                                  static_cast<int>(i));
            }
            lua_rawgeti(L, -1, 1);
            lua_rawgeti(L, -2, 2);
            lua_rawgeti(L, -3, 3);
            int start_ok = 0;
            int length_ok = 0;
            const lua_Integer start = lua_tointegerx(L, -3, &start_ok);
            const lua_Integer length = lua_tointegerx(L, -2, &length_ok);
            std::size_t size = 0;
            const char* reading = lua_tolstring(L, -1, &size);
            // Whole numbers that fit, not 1.5 read as 0 or 2^32 + 1 read as 1.
            if (!start_ok || !length_ok || start < 0 || length <= 0 || start > 0xFFFFFFFF ||
                length > 0xFFFFFFFF || !reading) {
                return luaL_error(L, "emit: ruby[%d] must be {start, length, reading}",
                                  static_cast<int>(i));
            }
            event.ruby.push_back(TextEvent::Ruby{static_cast<u32>(start), static_cast<u32>(length),
                                                 std::string(reading, size)});
            lua_pop(L, 4);
        }
    } else if (!lua_isnil(L, -1)) {
        return luaL_error(L, "emit: field 'ruby' must be a table");
    }
    lua_pop(L, 1);

    lua_getfield(L, 1, "meta");
    if (lua_istable(L, -1)) {
        std::map<std::string, std::string> entries; // sorted for stable output
        lua_pushnil(L);
        while (lua_next(L, -2) != 0) {
            if (lua_type(L, -2) == LUA_TSTRING) {
                std::string value;
                switch (lua_type(L, -1)) {
                case LUA_TBOOLEAN:
                    value = lua_toboolean(L, -1) ? "true" : "false";
                    break;
                case LUA_TNUMBER:
                    if (lua_isinteger(L, -1)) {
                        value = std::to_string(static_cast<s64>(lua_tointeger(L, -1)));
                    } else {
                        const double number = lua_tonumber(L, -1);
                        value = std::isfinite(number) ? NumberText(number) : "null";
                    }
                    break;
                case LUA_TSTRING: {
                    std::size_t size = 0;
                    const char* data = lua_tolstring(L, -1, &size);
                    value = "\"";
                    AppendJsonString(value, SanitizeUtf8(std::string_view(data, size)));
                    value.push_back('"');
                    break;
                }
                default:
                    break;
                }
                if (!value.empty()) {
                    entries.emplace(SanitizeUtf8(lua_tostring(L, -2)), std::move(value));
                }
            }
            lua_pop(L, 1);
        }
        if (!entries.empty()) {
            event.meta_json = "{";
            for (const auto& [key, value] : entries) {
                if (event.meta_json.size() > 1) {
                    event.meta_json.push_back(',');
                }
                event.meta_json.push_back('"');
                AppendJsonString(event.meta_json, key);
                event.meta_json += "\":";
                event.meta_json += value;
            }
            event.meta_json.push_back('}');
        }
    } else if (!lua_isnil(L, -1)) {
        return luaL_error(L, "emit: field 'meta' must be a table");
    }
    lua_pop(L, 1);

    GetEngine(L).Emit(std::move(event));
    return 0;
}

/// Reads {x, y, width, height} from the table at `index`.
bool ReadRect(lua_State* L, int index, ScreenRect& out) {
    index = lua_absindex(L, index);
    float values[4]{};
    for (int i = 0; i < 4; ++i) {
        lua_rawgeti(L, index, i + 1);
        const bool number = lua_type(L, -1) == LUA_TNUMBER;
        values[i] = number ? static_cast<float>(lua_tonumber(L, -1)) : 0.0f;
        lua_pop(L, 1);
        if (!number) {
            return false;
        }
    }
    out = ScreenRect{values[0], values[1], values[2], values[3]};
    return true;
}

/// More glyphs than this in one text box is a mistake in the script.
constexpr lua_Integer MaxLayoutGlyphs = 4096;

int ReaderLayout(lua_State* L) {
    const int key_type = lua_type(L, 1);
    luaL_argcheck(L, key_type == LUA_TSTRING || key_type == LUA_TNUMBER, 1,
                  "window key (string or number) expected");
    lua_pushvalue(L, 1); // convert a copy, not the argument
    std::size_t size = 0;
    const char* key = lua_tolstring(L, -1, &size);
    std::string window(key, size);
    lua_pop(L, 1);

    Engine& engine = GetEngine(L);
    if (lua_isnoneornil(L, 2)) {
        engine.CloseLayout(window);
        return 0;
    }
    luaL_checktype(L, 2, LUA_TTABLE);
    const Screens& screens = engine.GetScreens();
    TextLayout layout;
    layout.window = std::move(window);
    layout.space_width = screens.width;
    layout.space_height = screens.height;

    // A console with two screens: which one the text is on (the upper one unless it says).
    lua_getfield(L, 2, "screen");
    if (lua_type(L, -1) == LUA_TSTRING) {
        const std::string_view name = lua_tostring(L, -1);
        if (name == "bottom" && screens.lower_width > 0) {
            layout.screen = 1;
            layout.space_width = screens.lower_width;
            layout.space_height = screens.lower_height;
        } else if (name != "top") {
            return luaL_error(L, screens.lower_width > 0
                                     ? "layout: screen must be \"top\" or \"bottom\""
                                     : "layout: this console has one screen: \"top\" or none");
        }
    } else if (!lua_isnil(L, -1)) {
        return luaL_error(L, "layout: screen must be \"top\" or \"bottom\"");
    }
    lua_pop(L, 1);

    lua_getfield(L, 2, "space");
    if (lua_istable(L, -1)) {
        lua_rawgeti(L, -1, 1);
        lua_rawgeti(L, -2, 2);
        if (lua_type(L, -2) != LUA_TNUMBER || lua_type(L, -1) != LUA_TNUMBER) {
            return luaL_error(L, "layout: space must be {width, height}");
        }
        layout.space_width = static_cast<float>(lua_tonumber(L, -2));
        layout.space_height = static_cast<float>(lua_tonumber(L, -1));
        // A picture has a size: anything else is a mistake to be told about, not to be dropped.
        if (!(layout.space_width >= 1.0f && layout.space_width <= 16384.0f) ||
            !(layout.space_height >= 1.0f && layout.space_height <= 16384.0f)) {
            return luaL_error(L, "layout: space must be {width, height}");
        }
        lua_pop(L, 2);
    } else if (!lua_isnil(L, -1)) {
        return luaL_error(L, "layout: space must be {width, height}");
    }
    lua_pop(L, 1);
    if (layout.space_width <= 0.0f) {
        return luaL_error(L, "layout: give space = {width, height}: this platform has no size of its own here");
    }

    lua_getfield(L, 2, "box");
    if (lua_istable(L, -1)) {
        ScreenRect box;
        if (!ReadRect(L, -1, box)) {
            return luaL_error(L, "layout: box must be {x, y, width, height}");
        }
        layout.box = box;
    } else if (!lua_isnil(L, -1)) {
        return luaL_error(L, "layout: box must be {x, y, width, height}");
    }
    lua_pop(L, 1);

    lua_getfield(L, 2, "glyphs");
    if (!lua_istable(L, -1)) {
        return luaL_error(L, "layout: glyphs must be a list with an entry per character");
    }
    const lua_Integer count = luaL_len(L, -1);
    if (count < 0 || count > MaxLayoutGlyphs) {
        return luaL_error(L, "layout: too many glyphs");
    }
    layout.glyphs.reserve(static_cast<std::size_t>(count));
    for (lua_Integer i = 1; i <= count; ++i) {
        lua_rawgeti(L, -1, i);
        ScreenRect rect;
        if (lua_istable(L, -1) && ReadRect(L, -1, rect)) {
            layout.glyphs.emplace_back(rect);
        } else {
            layout.glyphs.emplace_back(std::nullopt);
        }
        lua_pop(L, 1);
    }
    lua_pop(L, 1);

    lua_pushboolean(L, engine.ShowLayout(std::move(layout)));
    return 1;
}

int LogWith(lua_State* L, LogLevel level) {
    std::string message;
    const int count = lua_gettop(L);
    for (int i = 1; i <= count; ++i) {
        std::size_t size = 0;
        const char* part = luaL_tolstring(L, i, &size);
        if (i > 1) {
            message.push_back(' ');
        }
        message.append(part, size);
        lua_pop(L, 1);
    }
    GetEngine(L).Log(level, SanitizeUtf8(message));
    return 0;
}

int ReaderLog(lua_State* L) {
    return LogWith(L, LogLevel::Info);
}

int ReaderWarn(lua_State* L) {
    return LogWith(L, LogLevel::Warning);
}

int ReaderFrame(lua_State* L) {
    lua_pushinteger(L, static_cast<lua_Integer>(GetEngine(L).Frame()));
    return 1;
}

/// reader.picture() -> width, height of the picture the game hands over for display, or nothing
/// before it has shown one.
int ReaderPicture(lua_State* L) {
    const auto [width, height] = GetEngine(L).Picture();
    if (width == 0 || height == 0) {
        return 0;
    }
    lua_pushinteger(L, static_cast<lua_Integer>(width));
    lua_pushinteger(L, static_cast<lua_Integer>(height));
    return 2;
}

int ReaderData(lua_State* L) {
    const std::string name = luaL_checkstring(L, 1);
    const auto data = GetEngine(L).LoadData(name);
    if (data) {
        PushString(L, *data);
    } else {
        lua_pushnil(L);
    }
    return 1;
}

// ---- mem ----

static_assert(std::endian::native == std::endian::little, "the reads below are written for a little-endian host");

/// What a read reads from: the game's memory by address, or a region by offset when the function was
/// made by mem.region (its name is the function's upvalue).
std::string ReadFrom(lua_State* L, u64 address, std::size_t size) {
    const Engine& engine = GetEngine(L);
    if (lua_type(L, lua_upvalueindex(1)) == LUA_TSTRING) {
        return engine.ReadRegion(lua_tostring(L, lua_upvalueindex(1)), address, size);
    }
    return engine.ReadMemory(address, size);
}

template <std::size_t Size, bool Big, bool Signed>
int MemInteger(lua_State* L) {
    const u64 address = CheckAddress(L, 1);
    const std::string bytes = ReadFrom(L, address, Size);
    if (bytes.size() != Size) {
        lua_pushnil(L);
        return 1;
    }
    u64 value = 0;
    for (std::size_t i = 0; i < Size; ++i) {
        value |= static_cast<u64>(static_cast<u8>(bytes[Big ? Size - 1 - i : i])) << (8 * i);
    }
    if (Signed && Size < 8 && (value >> (8 * Size - 1)) != 0) {
        value |= ~u64{0} << (8 * Size);
    }
    lua_pushinteger(L, static_cast<lua_Integer>(value));
    return 1;
}

/// mem.w8 … mem.w64: Furoshiki writes for what the player asked. This reader never writes.
int MemWrite(lua_State* L) {
    return luaL_error(L, "the game's memory is read-only here: a script reads, it never writes");
}

template <typename Float, bool Big>
int MemFloat(lua_State* L) {
    const u64 address = CheckAddress(L, 1);
    std::string bytes = ReadFrom(L, address, sizeof(Float));
    if (bytes.size() != sizeof(Float)) {
        lua_pushnil(L);
        return 1;
    }
    if (Big) {
        std::reverse(bytes.begin(), bytes.end());
    }
    Float value;
    std::memcpy(&value, bytes.data(), sizeof(Float));
    lua_pushnumber(L, static_cast<lua_Number>(value));
    return 1;
}

int MemRead(lua_State* L) {
    const u64 address = CheckAddress(L, 1);
    const lua_Integer size = luaL_checkinteger(L, 2);
    luaL_argcheck(L, size >= 0 && size <= MaxStringRead, 2, "size out of range");
    PushString(L, ReadFrom(L, address, static_cast<std::size_t>(size)));
    return 1;
}

/// Reads units of `unit` bytes until an all-zero unit, the limit or unmapped memory.
int ReadTerminated(lua_State* L, std::size_t unit) {
    const u64 address = CheckAddress(L, 1);
    const lua_Integer limit = luaL_optinteger(L, 2, 4096);
    luaL_argcheck(L, limit >= 0 && limit <= MaxStringRead, 2, "limit out of range");

    std::string out;
    const std::size_t max_bytes = static_cast<std::size_t>(limit);
    bool terminated = false;
    while (!terminated && out.size() < max_bytes) {
        const u64 cursor = address + out.size();
        if (cursor < address) {
            break;
        }
        // Chunks keep unit alignment relative to the start address.
        const std::size_t want = std::min<std::size_t>(256, max_bytes - out.size());
        const std::string chunk = ReadFrom(L, cursor, want);
        std::size_t used = 0;
        for (; used + unit <= chunk.size(); used += unit) {
            bool zero = true;
            for (std::size_t k = 0; k < unit; ++k) {
                zero = zero && chunk[used + k] == '\0';
            }
            if (zero) {
                terminated = true;
                break;
            }
        }
        out.append(chunk, 0, used);
        if (chunk.size() < want || used == 0) {
            break;
        }
    }
    PushString(L, out);
    return 1;
}

int MemCstr(lua_State* L) {
    return ReadTerminated(L, 1);
}

int MemWstr(lua_State* L) {
    return ReadTerminated(L, 2);
}

/// The reads, for the game's memory and for each region alike (ReadFrom).
const luaL_Reg ReadFunctions[] = {
    {"u8", MemInteger<1, false, false>},    {"u16", MemInteger<2, false, false>},
    {"u32", MemInteger<4, false, false>},   {"u64", MemInteger<8, false, false>},
    {"s8", MemInteger<1, false, true>},     {"s16", MemInteger<2, false, true>},
    {"s32", MemInteger<4, false, true>},    {"u16be", MemInteger<2, true, false>},
    {"u32be", MemInteger<4, true, false>},  {"u64be", MemInteger<8, true, false>},
    {"s16be", MemInteger<2, true, true>},   {"s32be", MemInteger<4, true, true>},
    {"f32", MemFloat<float, false>},        {"f64", MemFloat<double, false>},
    {"f32be", MemFloat<float, true>},       {"f64be", MemFloat<double, true>},
    {"read", MemRead},                      {"cstr", MemCstr},
    {"wstr", MemWstr},                      {nullptr, nullptr},
};

/// mem.region(name): memory that has no address of the game's (video memory), read by offset; nil
/// where the host hasn't got it.
int MemRegion(lua_State* L) {
    std::size_t size = 0;
    const char* name = luaL_checklstring(L, 1, &size);
    const std::string_view view{name, size};
    const bool plain = !view.empty() && view.size() <= 32 &&
                       std::all_of(view.begin(), view.end(), [](char c) {
                           return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
                       });
    luaL_argcheck(L, plain, 1, "a region's name: a-z, 0-9 and _");
    const auto region_size = GetEngine(L).RegionSize(std::string{view});
    if (!region_size) {
        lua_pushnil(L);
        return 1;
    }
    lua_createtable(L, 0, 24);
    lua_pushvalue(L, 1);
    luaL_setfuncs(L, ReadFunctions, 1);
    lua_pushvalue(L, 1);
    lua_setfield(L, -2, "name");
    lua_pushinteger(L, static_cast<lua_Integer>(*region_size));
    lua_setfield(L, -2, "size");
    return 1;
}

// ---- text ----

int TextSjis(lua_State* L) {
    PushString(L, SjisToUtf8(CheckBytes(L, 1)));
    return 1;
}

int TextEucJp(lua_State* L) {
    PushString(L, EucJpToUtf8(CheckBytes(L, 1)));
    return 1;
}

int TextUtf16le(lua_State* L) {
    PushString(L, Utf16ToUtf8(CheckBytes(L, 1), false));
    return 1;
}

int TextUtf16be(lua_State* L) {
    PushString(L, Utf16ToUtf8(CheckBytes(L, 1), true));
    return 1;
}

int TextHash(lua_State* L) {
    PushString(L, HashHex(CheckBytes(L, 1)));
    return 1;
}

int TextLen(lua_State* L) {
    std::size_t size = 0;
    const char* data = luaL_checklstring(L, 1, &size);
    lua_pushinteger(L, static_cast<lua_Integer>(CountCodePoints(std::string_view(data, size))));
    return 1;
}

const luaL_Reg ReaderFunctions[] = {
    {"hook", ReaderHook},   {"on_frame", ReaderOnFrame},
    {"emit", ReaderEmit},   {"log", ReaderLog},          {"warn", ReaderWarn},
    {"frame", ReaderFrame}, {"data", ReaderData},        {"layout", ReaderLayout},
    {"picture", ReaderPicture}, {"on_state_loaded", ReaderOnStateLoaded},
    {"progress", ReaderProgress}, {"cursor", ReaderCursor}, {"action", ReaderAction},
    {nullptr, nullptr},
};

const luaL_Reg MemFunctions[] = {
    {"w8", MemWrite}, {"w16", MemWrite}, {"w32", MemWrite}, {"w64", MemWrite},
    {"region", MemRegion}, {nullptr, nullptr},
};

const luaL_Reg TextFunctions[] = {
    {"sjis", TextSjis}, {"eucjp", TextEucJp}, {"utf16le", TextUtf16le}, {"utf16be", TextUtf16be},
    {"hash", TextHash}, {"len", TextLen},         {nullptr, nullptr},
};

/// Helpers that are simpler to express in Lua than through the C API.
const char Prelude[] = R"lua(
local budget_exhausted = ...

-- A callback that ran out of budget ends, even when the error passes through a pcall of the
-- script's own.
do
    local raw_pcall, raw_xpcall, raw_resume, error = pcall, xpcall, coroutine.resume, error
    local function pass(ok, ...)
        if not ok and budget_exhausted() then
            error((...), 0)
        end
        return ok, ...
    end
    function pcall(f, ...)
        return pass(raw_pcall(f, ...))
    end
    function xpcall(f, handler, ...)
        return pass(raw_xpcall(f, handler, ...))
    end
    function coroutine.resume(co, ...)
        return pass(raw_resume(co, ...))
    end
end

local Builder = {}
Builder.__index = Builder

-- Accumulates display text and ruby spans; offsets are counted in code points.
function reader.builder()
    return setmetatable({ parts = {}, count = 0, spans = {} }, Builder)
end

function Builder:add(s)
    if s ~= nil and s ~= "" then
        self.parts[#self.parts + 1] = s
        self.count = self.count + text.len(s)
    end
    return self
end

function Builder:add_ruby(base, reading)
    local length = text.len(base)
    if length > 0 and reading ~= nil and reading ~= "" then
        self.spans[#self.spans + 1] = { self.count, length, reading }
    end
    return self:add(base)
end

function Builder:text()
    return table.concat(self.parts)
end

function Builder:ruby()
    return self.spans
end

function Builder:length()
    return self.count
end

-- Compiled chunks are not checked by Lua and could corrupt memory: source text only.
do
    local raw_load, select = load, select
    function load(chunk, name, mode, ...)
        if select("#", ...) == 0 then
            return raw_load(chunk, name, "t")
        end
        return raw_load(chunk, name, "t", (...))
    end
end

-- The same memory gives the same events: random numbers start from the same seed in every run.
do
    local seed = math.randomseed
    seed(0)
    function math.randomseed(a, b)
        return seed(a or 0, b or 0)
    end
end

-- There is no console; print goes to the log.
function print(...)
    reader.log(...)
end

local last_values = {}

-- True when `value` differs from the value last passed for `slot`.
function reader.changed(slot, value)
    if last_values[slot] == value then
        return false
    end
    last_values[slot] = value
    return true
end

function reader.forget(slot)
    if slot == nil then
        last_values = {}
    else
        last_values[slot] = nil
    end
end
)lua";

/// Out of memory, or an error, outside a protected call: Lua would end the process. An exception
/// instead, which the functions of yomikata_reader.h catch (Lua is built as C++, so it unwinds).
int Panic(lua_State*) {
    throw std::runtime_error("the script's Lua state failed outside a protected call");
}

/// lua_CFunction: sets up the state at index 1 (the engine, as light userdata).
int Install(lua_State* L) {
    Engine& engine = *static_cast<Engine*>(lua_touserdata(L, 1));

    static const luaL_Reg libraries[] = {
        {LUA_GNAME, luaopen_base},        {LUA_TABLIBNAME, luaopen_table},
        {LUA_STRLIBNAME, luaopen_string}, {LUA_MATHLIBNAME, luaopen_math},
        {LUA_UTF8LIBNAME, luaopen_utf8},  {LUA_COLIBNAME, luaopen_coroutine},
    };
    for (const auto& library : libraries) {
        luaL_requiref(L, library.name, library.func, 1);
        lua_pop(L, 1);
    }
    // Scripts get no file or process access.
    for (const char* name : {"dofile", "loadfile"}) {
        lua_pushnil(L);
        lua_setglobal(L, name);
    }

    lua_pushlightuserdata(L, &engine);
    lua_rawsetp(L, LUA_REGISTRYINDEX, &EngineKey);

    luaL_newlib(L, ReaderFunctions);
    const std::string title = engine.GameId();
    lua_pushlstring(L, title.data(), title.size());
    lua_setfield(L, -2, "game_id");
    lua_pushlstring(L, title.data(), title.size());
    lua_setfield(L, -2, "title_id"); // the name it has in the reader's Switch and 3DS siblings
    // The game's own program: where it is, and which build it is, where the host knows.
    if (const auto& base = engine.Base()) {
        lua_pushinteger(L, static_cast<lua_Integer>(*base));
        lua_setfield(L, -2, "base");
    }
    if (const auto& build_id = engine.BuildId()) {
        lua_pushlstring(L, build_id->data(), build_id->size());
        lua_setfield(L, -2, "build_id");
    }
    lua_setglobal(L, "reader");

    luaL_newlib(L, MemFunctions);
    lua_pushnil(L); // no region: the game's memory
    luaL_setfuncs(L, ReadFunctions, 1);
    lua_setglobal(L, "mem");

    luaL_newlib(L, TextFunctions);
    lua_setglobal(L, "text");

    lua_sethook(L, BudgetHook, LUA_MASKCOUNT, BudgetStep);

    if (luaL_loadbufferx(L, Prelude, sizeof(Prelude) - 1, "=reader prelude", "t") != LUA_OK) {
        return lua_error(L);
    }
    lua_pushcfunction(L, BudgetExhausted);
    lua_call(L, 1, 0);
    return 0;
}

} // Anonymous namespace

lua_State* NewState(Engine& engine) {
    const Limits& limits = engine.GetLimits();
    const auto steps = [](u64 instructions) {
        return static_cast<std::intptr_t>(std::clamp<u64>(instructions / BudgetStep, 1, INTPTR_MAX));
    };
    auto data = std::make_unique<StateData>();
    data->max_memory = static_cast<std::size_t>(std::min<u64>(limits.memory, SIZE_MAX));
    data->load_steps = steps(limits.load_instructions);
    data->callback_steps = steps(limits.call_instructions);
    lua_State* L = lua_newstate(LimitedAlloc, data.get());
    if (!L) {
        return nullptr;
    }
    data->budget = data->load_steps;
    data.release(); // owned by the state from here on, see CloseState
    lua_atpanic(L, Panic);

    lua_pushcfunction(L, Install);
    lua_pushlightuserdata(L, &engine);
    if (lua_pcall(L, 1, 0, 0) != LUA_OK) {
        const char* message = lua_tostring(L, -1);
        engine.Log(LogLevel::Error,
                   std::string("Lua state setup failed: ") + (message ? message : "unknown error"));
        CloseState(L);
        return nullptr;
    }
    return L;
}

void CloseState(lua_State* L) {
    void* ud = nullptr;
    lua_getallocf(L, &ud);
    lua_close(L);
    delete static_cast<StateData*>(ud);
}

void ResetBudget(lua_State* L, Budget kind) {
    StateData& data = DataOf(L);
    data.budget = kind == Budget::Load ? data.load_steps : data.callback_steps;
}

int CallAndFormat(lua_State* L) {
    lua_call(L, 0, LUA_MULTRET);
    const int count = lua_gettop(L);
    luaL_Buffer buffer;
    luaL_buffinit(L, &buffer);
    for (int i = 1; i <= count; ++i) {
        if (i > 1) {
            luaL_addchar(&buffer, '\t');
        }
        luaL_tolstring(L, i, nullptr);
        luaL_addvalue(&buffer);
    }
    luaL_pushresult(&buffer);
    return 1;
}

int Traceback(lua_State* L) {
    const char* message = lua_tostring(L, 1);
    if (!message) {
        message = luaL_tolstring(L, 1, nullptr);
    }
    luaL_traceback(L, L, message, 1);
    return 1;
}

} // namespace Reader::LuaApi
