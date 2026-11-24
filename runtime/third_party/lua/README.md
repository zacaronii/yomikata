# Lua 5.4.8

The sources of Lua 5.4.8 as released (https://www.lua.org/ftp/lua-5.4.8.tar.gz, SHA-256
`4f18ddae154e793e46eeab727c59ef1c0c0c2b744e7b94219710d76f530629ae`), unchanged, without the files the reader
doesn't build: the `lua` and `luac` programs (`lua.c`, `luac.c`) and the libraries a script doesn't get
(`liolib.c`, `loslib.c`, `loadlib.c`, `ldblib.c`, `linit.c`).

They are compiled as C++ (see `../../CMakeLists.txt`), so a Lua error is a C++ exception.

Lua is under the MIT licence: see the end of `lua.h`, and `NOTICE.md` at the root of this repository.
