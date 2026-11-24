# SPDX-FileCopyrightText: Yomikata contributors
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The Yomikata reader: one library, with Lua 5.4 inside it and only the functions of
# include/yomikata_reader.h showing. See README.md for building it, for Android too.

cmake_minimum_required(VERSION 3.20)
project(yomikata_reader VERSION 0.1.0 LANGUAGES CXX)

option(YOMIKATA_READER_SHARED "Build a shared library (off: a static one, to link into an app's own)" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Lua, compiled as C++: its errors are then exceptions, which unwind through the bindings, and its names
# are C++ names inside this library, so another Lua in the same process (an emulator's own) is not this one.
set(LUA_DIR ${CMAKE_CURRENT_SOURCE_DIR}/third_party/lua)
file(GLOB LUA_SOURCES CONFIGURE_DEPENDS ${LUA_DIR}/*.c)
set_source_files_properties(${LUA_SOURCES} PROPERTIES LANGUAGE CXX)

set(READER_SOURCES src/capi.cpp src/engine.cpp src/lua_api.cpp src/text_codec.cpp)

if(YOMIKATA_READER_SHARED)
  add_library(yomikata_reader SHARED ${READER_SOURCES} ${LUA_SOURCES})
else()
  add_library(yomikata_reader STATIC ${READER_SOURCES} ${LUA_SOURCES})
endif()
target_include_directories(yomikata_reader PUBLIC include PRIVATE src ${LUA_DIR})
target_compile_definitions(yomikata_reader PRIVATE YK_BUILD YK_VERSION="${PROJECT_VERSION}"
                                                   LUA_USER_H="lua_user.h")
if(NOT MSVC)
  set_source_files_properties(${READER_SOURCES} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra")
  if(YOMIKATA_READER_SHARED AND NOT APPLE)
    target_link_options(yomikata_reader PRIVATE -Wl,--no-undefined)
    # Only the yk_ functions show: nothing of Lua, and nothing of a C++ library linked into it.
    include(CheckLinkerFlag)
    set(YOMIKATA_EXPORTS -Wl,--version-script=${CMAKE_CURRENT_SOURCE_DIR}/exports.map)
    check_linker_flag(CXX "${YOMIKATA_EXPORTS}" YOMIKATA_HAS_VERSION_SCRIPT)
    if(YOMIKATA_HAS_VERSION_SCRIPT)
      target_link_options(yomikata_reader PRIVATE ${YOMIKATA_EXPORTS})
      set_property(TARGET yomikata_reader APPEND PROPERTY LINK_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/exports.map)
    endif()
    check_linker_flag(CXX "-Wl,--exclude-libs,ALL" YOMIKATA_HAS_EXCLUDE_LIBS)
    if(YOMIKATA_HAS_EXCLUDE_LIBS)
      target_link_options(yomikata_reader PRIVATE -Wl,--exclude-libs,ALL)
    endif()
  endif()
endif()
if(ANDROID AND YOMIKATA_READER_SHARED)
  # Loads on devices with 16 KB pages.
  target_link_options(yomikata_reader PRIVATE -Wl,-z,max-page-size=16384)
endif()
