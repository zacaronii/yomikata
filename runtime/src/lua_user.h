/* SPDX-FileCopyrightText: Yomikata contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Included by Lua's own sources (LUA_USER_H). Lua seeds the hashes of its strings from the clock and from
 * addresses, which makes the order `pairs` walks a table in differ from run to run. A script must give the
 * same events from the same memory, so the seed is fixed. */
#ifndef YOMIKATA_LUA_USER_H
#define YOMIKATA_LUA_USER_H

#define luai_makeseed(L) 0x594B5244u

#endif
