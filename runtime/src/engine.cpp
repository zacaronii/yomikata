// SPDX-FileCopyrightText: Yomikata contributors
// SPDX-FileCopyrightText: Flycast Reader fork contributors (from the PPSSPP and Eden Reader forks)
// SPDX-License-Identifier: GPL-2.0-or-later
//
// See engine.h. The JSON of events and layouts, Emit, ShowLayout, CloseLayout and the way a failing
// callback is switched off follow Furoshiki's engine.cpp.

#include <algorithm>
#include <charconv>
#include <cmath>
#include <span>
#include <stdexcept>
#include "engine.h"
#include "lua_api.h"
#include "text_codec.h"

#include <lauxlib.h>
#include <lua.h>

namespace Reader {

namespace {

/// A handler that keeps failing is switched off so it cannot flood the log every frame.
constexpr u32 MaxConsecutiveFailures = 5;

/// Upper bound for a single memory read requested by a script.
constexpr std::size_t MaxReadSize = 16 * 1024 * 1024;

/// The shortest decimal that reads back as the same number.
void AppendNumber(std::string& out, double value) {
    char buffer[32];
    const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value);
    out.append(buffer, result.ptr);
}

std::string_view Strip(std::string_view text) {
    while (!text.empty() && (text.front() == ' ' || text.front() == '\t')) {
        text.remove_prefix(1);
    }
    while (!text.empty() && (text.back() == ' ' || text.back() == '\t' || text.back() == '\r')) {
        text.remove_suffix(1);
    }
    return text;
}

} // Anonymous namespace

std::string TextEvent::ToJson() const {
    std::string out;
    out.reserve(160 + text.size() + speaker.size());
    out += "{\"seq\":" + std::to_string(sequence) + ",\"frame\":" + std::to_string(frame) +
           ",\"channel\":\"";
    AppendJsonString(out, channel);
    out += "\",\"speaker\":\"";
    AppendJsonString(out, speaker);
    out += "\",\"text\":\"";
    AppendJsonString(out, text);
    out += "\",\"ruby\":[";
    for (std::size_t i = 0; i < ruby.size(); ++i) {
        if (i != 0) {
            out.push_back(',');
        }
        out += "[" + std::to_string(ruby[i].start) + "," + std::to_string(ruby[i].length) + ",\"";
        AppendJsonString(out, ruby[i].reading);
        out += "\"]";
    }
    out += "],\"id\":\"";
    AppendJsonString(out, source_id);
    out += "\",\"meta\":";
    out += meta_json.empty() ? "{}" : meta_json;
    if (!window.empty()) {
        out += ",\"window\":\"";
        AppendJsonString(out, window);
        out.push_back('"');
    }
    out.push_back('}');
    return out;
}

namespace {

/// Screen coordinates with at most two decimals, and none when they are whole.
void AppendCoordinate(std::string& out, float value) {
    const double rounded = std::round(static_cast<double>(value) * 100.0) / 100.0;
    AppendNumber(out, rounded == 0.0 ? 0.0 : rounded);
}

void AppendRect(std::string& out, const ScreenRect& rect) {
    out.push_back('[');
    AppendCoordinate(out, rect.x);
    out.push_back(',');
    AppendCoordinate(out, rect.y);
    out.push_back(',');
    AppendCoordinate(out, rect.width);
    out.push_back(',');
    AppendCoordinate(out, rect.height);
    out.push_back(']');
}

/// Layouts come from game memory by way of a script: keep nonsense away from hosts.
bool IsSane(const ScreenRect& rect) {
    const auto in_range = [](float v) { return std::isfinite(v) && v > -16384.0f && v < 16384.0f; };
    return in_range(rect.x) && in_range(rect.y) && in_range(rect.width) && in_range(rect.height) &&
           rect.width >= 0.0f && rect.height >= 0.0f;
}

bool IsSaneSpace(float value) {
    return std::isfinite(value) && value >= 1.0f && value <= 16384.0f;
}

} // Anonymous namespace

std::string TextLayout::ToJson() const {
    std::string out = "{\"window\":\"";
    AppendJsonString(out, window);
    out += "\",\"seq\":" + std::to_string(sequence);
    if (closed) {
        out += ",\"closed\":true}";
        return out;
    }
    if (screen == 1) {
        out += ",\"screen\":1";
    }
    out += ",\"space\":[";
    AppendCoordinate(out, space_width);
    out.push_back(',');
    AppendCoordinate(out, space_height);
    out.push_back(']');
    if (box) {
        out += ",\"box\":";
        AppendRect(out, *box);
    }
    out += ",\"glyphs\":[";
    for (std::size_t i = 0; i < glyphs.size(); ++i) {
        if (i != 0) {
            out.push_back(',');
        }
        if (glyphs[i]) {
            AppendRect(out, *glyphs[i]);
        } else {
            out += "null";
        }
    }
    out += "]}";
    return out;
}

Screens ScreensOf(std::string_view platform) {
    // The PlayStations, the Dreamcast and the 3DS as Furoshiki has them; the others their own pixels.
    if (platform == "ps1" || platform == "ps2" || platform == "psp" || platform == "dc") {
        return {1280, 720, 0, 0};
    }
    if (platform == "3ds") {
        return {400, 240, 320, 240};
    }
    if (platform == "nds") {
        return {256, 192, 256, 192};
    }
    if (platform == "gc" || platform == "wii") {
        return {640, 480, 0, 0};
    }
    if (platform == "gba") {
        return {240, 160, 0, 0};
    }
    if (platform == "sfc") {
        return {256, 224, 0, 0};
    }
    if (platform == "gb" || platform == "gbc") {
        return {160, 144, 0, 0};
    }
    return {};
}

std::vector<std::pair<std::string, std::string>> ParseHeader(std::string_view source) {
    std::vector<std::pair<std::string, std::string>> out;
    // Only the comments the file begins with are looked at.
    while (!source.empty()) {
        const std::size_t end = source.find('\n');
        std::string_view line = Strip(source.substr(0, end));
        source = end == std::string_view::npos ? std::string_view{} : source.substr(end + 1);
        if (line.empty()) {
            continue;
        }
        if (!line.starts_with("--")) {
            break;
        }
        line = Strip(line.substr(2));
        if (!line.starts_with("reader:")) {
            continue;
        }
        line = Strip(line.substr(7));
        const auto word = [&line] {
            const std::size_t end = line.find_first_of(" \t");
            const std::string_view first = line.substr(0, end);
            line = end == std::string_view::npos ? std::string_view{} : Strip(line.substr(end));
            return first;
        };
        std::string key{word()};
        if (key == "no" && !line.empty()) {
            key += " " + std::string{word()};
        }
        if (key.empty()) {
            continue;
        }
        out.emplace_back(std::move(key), std::string{line});
    }
    return out;
}

Engine::Engine(const yk_host& host_, const yk_options& options) : host{host_} {
    game_id = options.game_id ? options.game_id : "";
    if (options.build_id) {
        build_id = options.build_id;
    }
    if (options.has_base) {
        base = options.base;
    }
    if (options.load_instructions) {
        limits.load_instructions = options.load_instructions;
    }
    if (options.call_instructions) {
        limits.call_instructions = options.call_instructions;
    }
    if (options.memory) {
        limits.memory = options.memory;
    }
    screens = ScreensOf(options.platform ? options.platform : "");
}

Engine::~Engine() {
    Unload();
}

bool Engine::StartState() {
    lua = LuaApi::NewState(*this);
    if (!lua) {
        last_error = "out of memory creating the Lua state";
        return false;
    }
    return true;
}

namespace {
/// The error on top of the Lua stack as text, without asking Lua to make it text: turning a number into
/// a string allocates, and an allocation outside a protected call that fails (a script at its memory
/// cap) is a panic.
std::string ErrorText(lua_State* lua) {
    if (lua_type(lua, -1) == LUA_TSTRING) {
        std::size_t size = 0;
        const char* text = lua_tolstring(lua, -1, &size);
        return std::string(text, size);
    }
    if (lua_isinteger(lua, -1)) {
        return "error " + std::to_string(static_cast<long long>(lua_tointeger(lua, -1)));
    }
    if (lua_type(lua, -1) == LUA_TNUMBER) {
        std::string out = "error ";
        AppendNumber(out, static_cast<double>(lua_tonumber(lua, -1)));
        return out;
    }
    return std::string("a Lua error that is not text (") + lua_typename(lua, lua_type(lua, -1)) + ")";
}
} // namespace

bool Engine::Load(const std::string& chunk_name, const std::string& source) {
    Unload();
    last_error.clear();

    // A script that can't do without a region isn't started on a host that hasn't got it.
    for (const auto& [key, rest] : ParseHeader(source)) {
        if (key != "needs") {
            continue;
        }
        std::string_view names = rest;
        while (!(names = Strip(names)).empty()) {
            const std::size_t end = names.find_first_of(" \t,");
            const std::string name{names.substr(0, end)};
            names = end == std::string_view::npos ? std::string_view{} : names.substr(end + 1);
            if (!name.empty() && !RegionSize(name)) {
                last_error = "the script needs " + name + ", which this host doesn't give out";
                Log(LogLevel::Error, "script " + chunk_name + " not loaded: " + last_error);
                return false;
            }
        }
    }

    if (!StartState()) {
        return false;
    }
    const std::string name = "@" + chunk_name;
    bool ok = luaL_loadbufferx(lua, source.data(), source.size(), name.c_str(), "t") == LUA_OK;
    if (ok) {
        LuaApi::ResetBudget(lua, LuaApi::Budget::Load);
        ok = lua_pcall(lua, 0, 0, 0) == LUA_OK;
    }
    if (!ok) {
        const std::string error = ErrorText(lua);
        Log(LogLevel::Error, "script " + chunk_name + " failed to load: " + error);
        Unload();
        last_error = error;
        return false;
    }
    Log(LogLevel::Info, "script " + chunk_name + " loaded for " + game_id + ": " +
                            std::to_string(frame_callbacks.size()) + " frame callback(s)");
    return true;
}

bool Engine::Execute(const std::string& code, std::string& output) {
    if (!lua && !StartState()) {
        output = last_error;
        return false;
    }
    // An expression first ("return <code>"), then a statement.
    const std::string as_expression = "return " + code;
    if (luaL_loadbufferx(lua, as_expression.data(), as_expression.size(), "=eval", "t") != LUA_OK) {
        lua_pop(lua, 1);
        if (luaL_loadbufferx(lua, code.data(), code.size(), "=eval", "t") != LUA_OK) {
            output = ErrorText(lua);
            lua_pop(lua, 1);
            return false;
        }
    }
    LuaApi::ResetBudget(lua, LuaApi::Budget::Load);
    lua_pushcfunction(lua, LuaApi::CallAndFormat);
    lua_insert(lua, -2);
    const bool ok = lua_pcall(lua, 1, 1, 0) == LUA_OK;
    output = ErrorText(lua);
    lua_pop(lua, 1);
    return ok;
}

void Engine::Unload() {
    CloseAllLayouts();
    windows.clear();
    frame_callbacks.clear();
    state_callbacks.clear();
    SetCursor(std::nullopt);
    if (lua) {
        // Closing the state runs the script's finalizers, which may still call reader.*: what they
        // would add belongs to a state that is going, and is refused (closing).
        closing = true;
        LuaApi::CloseState(lua);
        lua = nullptr;
        closing = false;
    }
}

void Engine::OnStateLoaded() {
    CloseAllLayouts();
    windows.clear();
    if (!lua) {
        return;
    }
    for (std::size_t i = 0; i < state_callbacks.size(); ++i) {
        lua_rawgeti(lua, LUA_REGISTRYINDEX, state_callbacks[i]);
        CallTop(0, "on_state_loaded");
    }
}

void Engine::OnFrame(u64 frame_) {
    frame = frame_;
    if (!lua) {
        return;
    }
    // By index: a callback may register further callbacks.
    for (std::size_t i = 0; i < frame_callbacks.size(); ++i) {
        if (frame_callbacks[i].failures >= MaxConsecutiveFailures) {
            continue;
        }
        lua_rawgeti(lua, LUA_REGISTRYINDEX, frame_callbacks[i].lua_ref);
        lua_pushinteger(lua, static_cast<lua_Integer>(frame));
        const bool ok = CallTop(1, "on_frame");
        FrameCallback& callback = frame_callbacks[i];
        if (ok) {
            callback.failures = 0;
        } else if (++callback.failures >= MaxConsecutiveFailures) {
            Log(LogLevel::Error, "on_frame callback disabled after " +
                                     std::to_string(MaxConsecutiveFailures) + " consecutive errors");
        }
    }
}

bool Engine::CallTop(int arg_count, const char* what) {
    LuaApi::ResetBudget(lua);
    const int base_index = lua_gettop(lua) - arg_count;
    lua_pushcfunction(lua, LuaApi::Traceback);
    lua_insert(lua, base_index);
    const int status = lua_pcall(lua, arg_count, 0, base_index);
    if (status != LUA_OK) {
        last_error = ErrorText(lua);
        Log(LogLevel::Error, std::string(what) + " handler failed: " + last_error);
        lua_pop(lua, 1);
    }
    lua_remove(lua, base_index);
    return status == LUA_OK;
}

std::string Engine::ReadMemory(u64 address, std::size_t size) const {
    size = std::min(size, MaxReadSize);
    std::string out(size, '\0');
    const std::size_t got = size ? host.read(host.user, address, out.data(), size) : 0;
    out.resize(std::min(got, size));
    return out;
}

std::optional<u64> Engine::RegionSize(const std::string& name) const {
    if (!host.region_size || !host.region_read) {
        return std::nullopt;
    }
    const int64_t size = host.region_size(host.user, name.c_str());
    if (size < 0) {
        return std::nullopt;
    }
    return static_cast<u64>(size);
}

std::string Engine::ReadRegion(const std::string& name, u64 offset, std::size_t size) const {
    if (!host.region_read) {
        return {};
    }
    size = std::min(size, MaxReadSize);
    std::string out(size, '\0');
    const std::size_t got = size ? host.region_read(host.user, name.c_str(), offset, out.data(), size) : 0;
    out.resize(std::min(got, size));
    return out;
}

void Engine::AddStateCallback(int lua_ref) {
    if (closing) {
        return;
    }
    state_callbacks.push_back(lua_ref);
}

void Engine::AddFrameCallback(int lua_ref) {
    if (closing) {
        return;
    }
    frame_callbacks.push_back(FrameCallback{lua_ref, 0});
}

void Engine::SetCursor(std::optional<AnswerCursor> cursor_) {
    if (closing || cursor == cursor_) {
        return;
    }
    cursor = cursor_;
    if (!cursor) {
        Send(YK_CURSOR, "{\"closed\":true}");
        return;
    }
    Send(YK_CURSOR, "{\"index\":" + std::to_string(cursor->index) + ",\"horizontal\":" +
                        (cursor->horizontal ? "true" : "false") + "}");
}

void Engine::Emit(TextEvent event) {
    if (closing) {
        return;
    }
    event.text = SanitizeUtf8(event.text);
    if (event.text.empty()) {
        return;
    }
    event.speaker = SanitizeUtf8(event.speaker);
    event.channel = event.channel.empty() ? "dialogue" : SanitizeUtf8(event.channel);
    // Whoever receives the event may rely on ruby spans lying inside the text, in order and not
    // overlapping. A span that breaks this is dropped rather than the whole line.
    const std::size_t text_length = CountCodePoints(event.text);
    std::stable_sort(event.ruby.begin(), event.ruby.end(),
                     [](const auto& a, const auto& b) { return a.start < b.start; });
    std::vector<TextEvent::Ruby> spans;
    spans.reserve(event.ruby.size());
    u64 covered = 0;
    for (auto& ruby : event.ruby) {
        const u64 end = static_cast<u64>(ruby.start) + ruby.length;
        if (ruby.length == 0 || ruby.reading.empty() || ruby.start < covered || end > text_length) {
            Log(LogLevel::Warning, "ruby span " + std::to_string(ruby.start) + "+" +
                                       std::to_string(ruby.length) +
                                       " dropped: outside the text or overlapping");
            continue;
        }
        covered = end;
        ruby.reading = SanitizeUtf8(ruby.reading);
        spans.push_back(std::move(ruby));
    }
    event.ruby = std::move(spans);
    if (event.source_id.empty()) {
        const std::string key = event.channel + '\0' + event.speaker + '\0' + event.text;
        event.source_id =
            HashHex(std::span<const u8>{reinterpret_cast<const u8*>(key.data()), key.size()});
    } else {
        event.source_id = SanitizeUtf8(event.source_id);
    }
    event.sequence = next_sequence++;
    event.frame = frame;
    event.window = SanitizeUtf8(event.window);
    if (!event.window.empty()) {
        // New text in the window: a layout reported earlier was for the text it replaces.
        windows[event.window] = WindowText{event.sequence, text_length, false};
    }
    Send(YK_TEXT, event.ToJson());
}

bool Engine::ShowLayout(TextLayout layout) {
    if (closing) {
        return false;
    }
    layout.window = SanitizeUtf8(layout.window);
    const auto it = windows.find(layout.window);
    if (it == windows.end()) {
        return false;
    }
    WindowText& known = it->second;
    layout.sequence = known.sequence;
    layout.closed = false;
    if (!IsSaneSpace(layout.space_width) || !IsSaneSpace(layout.space_height)) {
        return false;
    }
    if (layout.box && !IsSane(*layout.box)) {
        layout.box.reset();
    }
    // One entry per code point of the text, no more and no fewer.
    layout.glyphs.resize(known.code_points);
    for (auto& glyph : layout.glyphs) {
        if (glyph && !IsSane(*glyph)) {
            glyph.reset();
        }
    }
    known.shown = true;
    Send(YK_LAYOUT, layout.ToJson());
    return true;
}

void Engine::CloseLayout(const std::string& window_) {
    const std::string window = SanitizeUtf8(window_);
    const auto it = windows.find(window);
    if (it == windows.end() || !it->second.shown) {
        return;
    }
    it->second.shown = false;
    TextLayout layout;
    layout.window = window;
    layout.sequence = it->second.sequence;
    layout.closed = true;
    Send(YK_LAYOUT, layout.ToJson());
}

void Engine::CloseAllLayouts() {
    std::vector<std::string> shown;
    for (const auto& [window, text] : windows) {
        if (text.shown) {
            shown.push_back(window);
        }
    }
    for (const auto& window : shown) {
        CloseLayout(window);
    }
}

void Engine::Log(LogLevel level, const std::string& message) const {
    std::string json = "{\"level\":\"";
    json += level == LogLevel::Info ? "info" : level == LogLevel::Warning ? "warning" : "error";
    json += "\",\"message\":\"";
    AppendJsonString(json, SanitizeUtf8(message));
    json += "\"}";
    Send(YK_LOG, json);
}

void Engine::Send(int kind, const std::string& json) const {
    host.event(host.user, kind, json.c_str(), json.size());
}

std::optional<std::string> Engine::LoadData(const std::string& name) const {
    if (!host.data) {
        return std::nullopt;
    }
    const int64_t size = host.data(host.user, name.c_str(), nullptr, 0);
    if (size < 0 || static_cast<u64>(size) > limits.memory) {
        return std::nullopt;
    }
    std::string out(static_cast<std::size_t>(size), '\0');
    const int64_t again = size ? host.data(host.user, name.c_str(), out.data(), out.size()) : 0;
    if (again != size) {
        return std::nullopt;
    }
    return out;
}

} // namespace Reader
