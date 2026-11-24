// SPDX-FileCopyrightText: Flycast Reader fork contributors (from the PPSSPP and Eden Reader forks)
// SPDX-FileCopyrightText: Yomikata contributors
// SPDX-License-Identifier: GPL-2.0-or-later
//
// From Furoshiki (shared/reader/core/lua_api.h), without what hooks, progress and actions needed.

#pragma once

struct lua_State;

namespace Reader {

class Engine;

namespace LuaApi {

/// Creates a Lua state for a script: memory-capped, with the sandboxed standard libraries and the
/// `reader`, `mem` and `text` tables. Returns nullptr when memory runs out.
lua_State* NewState(Engine& engine);
void CloseState(lua_State* L);

enum class Budget { Callback, Load };
/// Gives the state the instructions one callback (or the loading of a script) may use.
void ResetBudget(lua_State* L, Budget kind = Budget::Callback);

/// lua_CFunction: calls the function at index 1 and returns its results as one string, tab-separated.
int CallAndFormat(lua_State* L);
/// lua_CFunction: a message handler that adds a traceback.
int Traceback(lua_State* L);

} // namespace LuaApi
} // namespace Reader
