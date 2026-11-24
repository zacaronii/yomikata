// SPDX-FileCopyrightText: Yomikata contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// The C functions of yomikata_reader.h over the engine. No exception leaves them.

#include <new>
#include <string>
#include "engine.h"
#include "yomikata_reader.h"

struct yk_reader {
    Reader::Engine engine;
    std::string error;  ///< What yk_error returns
    std::string output; ///< What yk_eval returns
    yk_reader(const yk_host& host, const yk_options& options) : engine{host, options} {}
};

namespace {

/// Runs `call`. Should it throw (a Lua panic, memory), the script is dropped and the reason kept.
template <typename Call>
bool Guarded(yk_reader* reader, Call&& call) {
    try {
        call();
        return true;
    } catch (const std::exception& e) {
        reader->error = e.what();
    } catch (...) {
        reader->error = "unknown failure";
    }
    try {
        reader->engine.Unload();
    } catch (...) {
    }
    return false;
}

} // namespace

extern "C" {

const char* yk_version(void) {
    return YK_VERSION;
}

int yk_script_level(void) {
    return YK_SCRIPT_LEVEL;
}

yk_reader* yk_open(const yk_host* host, const yk_options* options) {
    if (!host || !host->read || !host->event || !options || options->abi != YK_ABI) {
        return nullptr;
    }
    try {
        return new yk_reader(*host, *options);
    } catch (...) {
        return nullptr;
    }
}

void yk_close(yk_reader* reader) {
    if (!reader) {
        return;
    }
    Guarded(reader, [&] { reader->engine.Unload(); });
    try {
        delete reader;
    } catch (...) {
    }
}

int yk_load(yk_reader* reader, const char* name, const char* source, size_t length) {
    if (!reader || !source) {
        return 0;
    }
    bool loaded = false;
    const bool ran = Guarded(reader, [&] {
        loaded = reader->engine.Load(name ? name : "script", std::string(source, length));
        reader->error = loaded ? "" : reader->engine.LastError();
    });
    return ran && loaded ? 1 : 0;
}

void yk_unload(yk_reader* reader) {
    if (reader) {
        Guarded(reader, [&] { reader->engine.Unload(); });
    }
}

const char* yk_error(yk_reader* reader) {
    return reader ? reader->error.c_str() : "";
}

void yk_frame(yk_reader* reader, uint64_t frame) {
    if (reader) {
        Guarded(reader, [&] { reader->engine.OnFrame(frame); });
    }
}

void yk_state_loaded(yk_reader* reader) {
    if (reader) {
        Guarded(reader, [&] { reader->engine.OnStateLoaded(); });
    }
}

void yk_picture(yk_reader* reader, uint32_t width, uint32_t height) {
    if (reader) {
        reader->engine.OnPicture(width, height);
    }
}

const char* yk_eval(yk_reader* reader, const char* code, size_t length, int* ok) {
    if (ok) {
        *ok = 0;
    }
    if (!reader || !code) {
        return "";
    }
    bool good = false;
    if (!Guarded(reader, [&] { good = reader->engine.Execute(std::string(code, length), reader->output); })) {
        reader->output = reader->error;
        good = false;
    }
    if (ok) {
        *ok = good ? 1 : 0;
    }
    return reader->output.c_str();
}

void yk_header(const char* source, size_t length, void (*line)(void* user, const char* key, const char* rest),
               void* user) {
    if (!source || !line) {
        return;
    }
    try {
        for (const auto& [key, rest] : Reader::ParseHeader(std::string_view(source, length))) {
            line(user, key.c_str(), rest.c_str());
        }
    } catch (...) {
    }
}

} // extern "C"
