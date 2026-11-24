// SPDX-FileCopyrightText: Yomikata contributors
// SPDX-FileCopyrightText: Flycast Reader fork contributors (from the PPSSPP and Eden Reader forks)
// SPDX-License-Identifier: GPL-2.0-or-later
//
// A smaller engine than Furoshiki's (shared/reader/core/engine.h), which TextEvent, TextLayout and the
// handling of emits and layouts are taken from: no hooks, no writes, no actions or progress, one engine
// per game instead of one per process, and a host given as C functions (yomikata_reader.h).

#pragma once

#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#include "types.h"
#include "yomikata_reader.h"

struct lua_State;

namespace Reader {

enum class LogLevel { Info, Warning, Error };

/// One piece of text presented by the game, as produced by a script.
struct TextEvent {
    struct Ruby {
        u32 start;  ///< Offset into `text`, in Unicode code points
        u32 length; ///< Length of the annotated base text, in Unicode code points
        std::string reading;
    };

    u64 sequence{};      ///< Increases by one for every event of the running game
    u64 frame{};         ///< Game frame on which the event was produced
    std::string channel; ///< "dialogue" unless the script says otherwise
    std::string speaker;
    std::string text; ///< Base text without ruby readings; lines separated by '\n'
    std::vector<Ruby> ruby;
    std::string source_id; ///< Stable identity of this text within the game
    std::string meta_json; ///< Flat JSON object with script-defined extras, or empty
    std::string window;    ///< What the text is shown in, when the script reports layouts

    /// Serialises the event as a single-line JSON object (valid UTF-8).
    [[nodiscard]] std::string ToJson() const;
};

/// A rectangle of the game's picture, in the units of TextLayout::space_width.
struct ScreenRect {
    float x{};
    float y{};
    float width{};
    float height{};
};

/// Where the text of an event is in the game's picture. Belongs to the latest event of `window`.
struct TextLayout {
    std::string window;
    u64 sequence{}; ///< Sequence number of the event the layout belongs to
    bool closed{};
    int screen{}; ///< 0 the (upper) screen, 1 the lower one of a console that has two
    float space_width{};
    float space_height{};
    std::optional<ScreenRect> box; ///< The text box as a whole, if known
    /// One entry per Unicode code point of the event's text; empty where nothing is drawn.
    std::vector<std::optional<ScreenRect>> glyphs;

    [[nodiscard]] std::string ToJson() const;
};

/// What a script may use (docs/script-api.md, "Limits").
struct Limits {
    u64 load_instructions{100'000'000};
    u64 call_instructions{2'000'000};
    u64 memory{32ull * 1024 * 1024};
};

/// A platform's picture, for a layout that doesn't give its `space`. Zeros: the script must give it.
struct Screens {
    float width{};
    float height{};
    float lower_width{}; ///< Above zero on a console with two screens
    float lower_height{};
};
[[nodiscard]] Screens ScreensOf(std::string_view platform);

/// A script's header lines: {key, rest} for each "-- reader: <key> <rest>" among the comments it begins
/// with. "no hooks" and "no layout" are keys of two words.
[[nodiscard]] std::vector<std::pair<std::string, std::string>> ParseHeader(std::string_view source);

/// Runs one game's script. Not safe to enter from two threads at once.
class Engine {
public:
    Engine(const yk_host& host, const yk_options& options);
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    /// Replaces the running script. Returns false and keeps no script when it fails.
    bool Load(const std::string& chunk_name, const std::string& source);
    void Unload();
    /// Runs a chunk in the script's Lua state, starting an empty one when none is loaded.
    bool Execute(const std::string& code, std::string& output);
    void OnFrame(u64 frame);
    void OnStateLoaded();
    void OnPicture(u32 width, u32 height) {
        picture = {width, height};
    }
    [[nodiscard]] const std::string& LastError() const {
        return last_error;
    }

    // ---- Used by the Lua bindings ----

    [[nodiscard]] const std::string& GameId() const {
        return game_id;
    }
    [[nodiscard]] const std::optional<std::string>& BuildId() const {
        return build_id;
    }
    [[nodiscard]] const std::optional<u64>& Base() const {
        return base;
    }
    [[nodiscard]] u64 Frame() const {
        return frame;
    }
    [[nodiscard]] std::pair<u32, u32> Picture() const {
        return picture;
    }
    [[nodiscard]] const Limits& GetLimits() const {
        return limits;
    }
    [[nodiscard]] const Screens& GetScreens() const {
        return screens;
    }
    /// Reads up to `size` bytes, fewer where memory isn't there.
    [[nodiscard]] std::string ReadMemory(u64 address, std::size_t size) const;
    /// A named region's size, or nothing when the host hasn't got it.
    [[nodiscard]] std::optional<u64> RegionSize(const std::string& name) const;
    [[nodiscard]] std::string ReadRegion(const std::string& name, u64 offset, std::size_t size) const;
    void AddFrameCallback(int lua_ref);
    void AddStateCallback(int lua_ref);
    void Emit(TextEvent event);
    /// Reports where the latest text of `layout.window` is on screen. Returns false when no text was
    /// emitted for that window.
    bool ShowLayout(TextLayout layout);
    void CloseLayout(const std::string& window);
    struct AnswerCursor {
        int index{};
        bool horizontal{};
        bool operator==(const AnswerCursor&) const = default;
    };
    void SetCursor(std::optional<AnswerCursor> cursor);
    void Log(LogLevel level, const std::string& message) const;
    [[nodiscard]] std::optional<std::string> LoadData(const std::string& name) const;

private:
    struct FrameCallback {
        int lua_ref;
        u32 failures;
    };
    /// What is known about a window that events were emitted for.
    struct WindowText {
        u64 sequence{};
        std::size_t code_points{};
        bool shown{}; ///< A layout was reported and not closed since
    };

    bool StartState();
    /// Calls the function below `arg_count` arguments on the Lua stack, reporting any error.
    bool CallTop(int arg_count, const char* what);
    void CloseAllLayouts();
    void Send(int kind, const std::string& json) const;

    yk_host host;
    std::string game_id;
    std::optional<std::string> build_id;
    std::optional<u64> base;
    Limits limits;
    Screens screens;
    u64 frame{};
    std::pair<u32, u32> picture{};
    u64 next_sequence{1};

    lua_State* lua{};
    bool closing{}; ///< The script's state is being closed: nothing of it may be added
    std::vector<FrameCallback> frame_callbacks;
    std::vector<int> state_callbacks;
    std::optional<AnswerCursor> cursor;
    std::map<std::string, WindowText> windows; ///< Ordered, so closing all of them is the same every run
    std::string last_error;
};

} // namespace Reader
