// CopyManager clipboard monitor (backend).
//
//  * Capture: records every text copy (Ctrl+C, right-click > Copy, etc.) in
//    history.json, following the contract in README.md.
//  * Paste shortcuts: <modifiers>+0-9 pastes History item 0-9 into the
//    focused window, honouring the paste preferences.
//  * Live settings: re-reads settings.json when it changes, or when the GUI
//    posts WM_APP_REFRESH, and reports what's active in the status file.
//
// The GUI (app.pyw) starts this automatically and it exits when the GUI does.
//
// Build:  build_monitor.bat
//         (g++ -std=c++17 -O2 -municode -mwindows -static -o bin/monitor.exe src/monitor.cpp -lshell32)
// Usage:  monitor.exe [--settings PATH] [--history PATH] [--log PATH] [--status PATH] [--parent-pid PID]
//         Paths default to the project layout, relative to the folder above monitor.exe.

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601  // Windows 7+: AddClipboardFormatListener, QueryFullProcessImageNameW
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

// ============================================================================
// Limits
// ============================================================================
static const size_t MAX_TEXT_CHARS = 2'000'000;   // bigger copies are skipped
static const size_t MAX_RICH_BYTES = 1'000'000;   // bigger HTML/RTF payloads are dropped (text is kept)
static const UINT DEBOUNCE_MS = 120;              // apps often fire several updates per copy
static const UINT SETTINGS_CHECK_MS = 1000;       // how often settings.json is checked for changes
static const ULONGLONG MODIFIER_WAIT_MS = 1000;   // max wait for the user to let go of the shortcut keys
static const UINT RESTORE_DELAY_MS = 600;         // time the target app gets to read the clipboard

// Window messages / timers / hotkey ids
static const UINT WM_APP_REFRESH = WM_APP + 1;    // posted by the GUI; wParam = refresh id
static const UINT_PTR TIMER_CLIPBOARD = 1, TIMER_SETTINGS = 2, TIMER_PASTE = 3, TIMER_RESTORE = 4;
static const int HOTKEY_BASE = 100;               // ids 100-109 = paste shortcuts for 0-9

// ============================================================================
// UTF-8 helpers
// ============================================================================
static std::string toUtf8(const wchar_t* w, size_t len) {
    if (len == 0) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w, (int)len, nullptr, 0, nullptr, nullptr);
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, (int)len, &s[0], n, nullptr, nullptr);
    return s;
}
static std::string toUtf8(const std::wstring& w) { return toUtf8(w.data(), w.size()); }

static bool isValidUtf8(const std::string& s) {
    return s.empty() ||
           MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), (int)s.size(), nullptr, 0) != 0;
}

static std::string ansiToUtf8(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_ACP, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(CP_ACP, 0, s.data(), (int)s.size(), &w[0], n);
    return toUtf8(w);
}

static void appendUtf8(std::string& out, uint32_t cp) {
    if (cp >= 0xD800 && cp <= 0xDFFF) cp = 0xFFFD;  // lone surrogate
    if (cp < 0x80) {
        out += (char)cp;
    } else if (cp < 0x800) {
        out += (char)(0xC0 | (cp >> 6));
        out += (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        out += (char)(0xE0 | (cp >> 12));
        out += (char)(0x80 | ((cp >> 6) & 0x3F));
        out += (char)(0x80 | (cp & 0x3F));
    } else {
        out += (char)(0xF0 | (cp >> 18));
        out += (char)(0x80 | ((cp >> 12) & 0x3F));
        out += (char)(0x80 | ((cp >> 6) & 0x3F));
        out += (char)(0x80 | (cp & 0x3F));
    }
}

// ============================================================================
// Minimal JSON (keeps object key order and unknown fields intact)
// ============================================================================
namespace json {

struct Value {
    enum Type { Null, Bool, Number, String, Array, Object };
    Type type = Null;
    bool boolean = false;
    std::string text;               // string contents, or the literal text of a number
    std::vector<Value> items;       // array items / object values
    std::vector<std::string> keys;  // object keys (parallel to items)

    static Value string(std::string s) { Value v; v.type = String; v.text = std::move(s); return v; }
    static Value number(long long n) { Value v; v.type = Number; v.text = std::to_string(n); return v; }
    static Value array() { Value v; v.type = Array; return v; }
    static Value object() { Value v; v.type = Object; return v; }
    static Value stringOrNull(const std::string& s) { return s.empty() ? Value() : string(s); }

    Value* get(const std::string& key) {
        if (type != Object) return nullptr;
        for (size_t i = 0; i < keys.size(); ++i)
            if (keys[i] == key) return &items[i];
        return nullptr;
    }
    void set(const std::string& key, Value v) {
        if (Value* existing = get(key)) {
            *existing = std::move(v);
        } else {
            keys.push_back(key);
            items.push_back(std::move(v));
        }
    }
};

class Parser {
public:
    explicit Parser(const std::string& s) : s_(s) {
        if (s_.size() >= 3 && (unsigned char)s_[0] == 0xEF && (unsigned char)s_[1] == 0xBB &&
            (unsigned char)s_[2] == 0xBF)
            i_ = 3;  // UTF-8 BOM
    }
    Value parse() {
        Value v = value();
        ws();
        if (i_ != s_.size()) fail("unexpected trailing characters");
        return v;
    }

private:
    const std::string& s_;
    size_t i_ = 0;

    [[noreturn]] void fail(const char* what) {
        throw std::runtime_error(std::string(what) + " at offset " + std::to_string(i_));
    }
    void ws() {
        while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\n' || s_[i_] == '\r')) ++i_;
    }
    bool literal(const char* lit) {
        size_t n = std::strlen(lit);
        if (s_.compare(i_, n, lit) == 0) { i_ += n; return true; }
        return false;
    }
    static bool isNumberChar(char c) {
        return (c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E';
    }
    Value value() {
        ws();
        if (i_ >= s_.size()) fail("unexpected end of JSON");
        char c = s_[i_];
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return Value::string(str());
        if (literal("true")) { Value v; v.type = Value::Bool; v.boolean = true; return v; }
        if (literal("false")) { Value v; v.type = Value::Bool; return v; }
        if (literal("null")) return Value();
        if (c == '-' || (c >= '0' && c <= '9')) {
            size_t start = i_;
            while (i_ < s_.size() && isNumberChar(s_[i_])) ++i_;
            Value v;
            v.type = Value::Number;
            v.text = s_.substr(start, i_ - start);
            return v;
        }
        fail("unexpected character");
    }
    Value object() {
        Value v = Value::object();
        ++i_;
        ws();
        if (i_ < s_.size() && s_[i_] == '}') { ++i_; return v; }
        for (;;) {
            ws();
            if (i_ >= s_.size() || s_[i_] != '"') fail("expected a key");
            std::string key = str();
            ws();
            if (i_ >= s_.size() || s_[i_] != ':') fail("expected ':'");
            ++i_;
            Value item = value();
            v.keys.push_back(std::move(key));
            v.items.push_back(std::move(item));
            ws();
            if (i_ >= s_.size()) fail("unexpected end of JSON");
            if (s_[i_] == ',') { ++i_; continue; }
            if (s_[i_] == '}') { ++i_; return v; }
            fail("expected ',' or '}'");
        }
    }
    Value array() {
        Value v = Value::array();
        ++i_;
        ws();
        if (i_ < s_.size() && s_[i_] == ']') { ++i_; return v; }
        for (;;) {
            v.items.push_back(value());
            ws();
            if (i_ >= s_.size()) fail("unexpected end of JSON");
            if (s_[i_] == ',') { ++i_; continue; }
            if (s_[i_] == ']') { ++i_; return v; }
            fail("expected ',' or ']'");
        }
    }
    uint32_t hex4() {
        if (i_ + 4 > s_.size()) fail("bad \\u escape");
        uint32_t v = 0;
        for (int k = 0; k < 4; ++k) {
            char c = s_[i_++];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (uint32_t)(c - '0');
            else if (c >= 'a' && c <= 'f') v |= (uint32_t)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (uint32_t)(c - 'A' + 10);
            else fail("bad \\u escape");
        }
        return v;
    }
    std::string str() {
        ++i_;  // opening quote
        std::string out;
        for (;;) {
            if (i_ >= s_.size()) fail("unterminated string");
            char c = s_[i_++];
            if (c == '"') return out;
            if (c != '\\') { out += c; continue; }
            if (i_ >= s_.size()) fail("bad escape");
            char e = s_[i_++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    uint32_t cp = hex4();
                    if (cp >= 0xD800 && cp <= 0xDBFF && s_.compare(i_, 2, "\\u") == 0) {
                        size_t save = i_;
                        i_ += 2;
                        uint32_t lo = hex4();
                        if (lo >= 0xDC00 && lo <= 0xDFFF) cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        else i_ = save;
                    }
                    appendUtf8(out, cp);
                    break;
                }
                default: fail("bad escape");
            }
        }
    }
};

static void escape(std::string& out, const std::string& s) {
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    out += '"';
}

// Same layout as Python's json.dump(indent=4, ensure_ascii=False).
static void write(std::string& out, const Value& v, int depth = 0) {
    const std::string pad((size_t)depth * 4, ' '), inner((size_t)(depth + 1) * 4, ' ');
    switch (v.type) {
        case Value::Null: out += "null"; break;
        case Value::Bool: out += v.boolean ? "true" : "false"; break;
        case Value::Number: out += v.text; break;
        case Value::String: escape(out, v.text); break;
        case Value::Array:
        case Value::Object: {
            bool isObject = v.type == Value::Object;
            if (v.items.empty()) { out += isObject ? "{}" : "[]"; break; }
            out += isObject ? "{\n" : "[\n";
            for (size_t i = 0; i < v.items.size(); ++i) {
                out += inner;
                if (isObject) { escape(out, v.keys[i]); out += ": "; }
                write(out, v.items[i], depth + 1);
                out += (i + 1 < v.items.size()) ? ",\n" : "\n";
            }
            out += pad;
            out += isObject ? "}" : "]";
            break;
        }
    }
}

}  // namespace json

// ============================================================================
// Files and logging
// ============================================================================
static std::wstring g_settingsPath, g_historyPath, g_logPath, g_statusPath;
static HANDLE g_historyLock = nullptr;  // shared with the GUI (src/store.py)

static bool readFile(const std::wstring& path, std::string& out, bool* missing = nullptr) {
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        DWORD err = GetLastError();
        if (missing) *missing = (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND);
        return false;
    }
    if (missing) *missing = false;
    LARGE_INTEGER size{};
    GetFileSizeEx(h, &size);
    out.assign((size_t)size.QuadPart, '\0');
    size_t offset = 0;
    bool ok = true;
    while (offset < out.size()) {
        DWORD chunk = (DWORD)std::min<size_t>(out.size() - offset, 1u << 20), got = 0;
        if (!ReadFile(h, &out[offset], chunk, &got, nullptr) || got == 0) { ok = false; break; }
        offset += got;
    }
    CloseHandle(h);
    return ok;
}

// Write to a temp file and swap it into place, so readers never see a half-written file.
static bool writeFileAtomic(const std::wstring& path, const std::string& data) {
    const std::wstring tmp = path + L".monitor.tmp";
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    BOOL ok = WriteFile(h, data.data(), (DWORD)data.size(), &written, nullptr) && written == data.size();
    FlushFileBuffers(h);
    CloseHandle(h);
    if (ok) {
        for (int attempt = 0; attempt < 20; ++attempt) {  // the GUI may be reading it right now
            if (MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
                return true;
            Sleep(25);
        }
    }
    DeleteFileW(tmp.c_str());
    return false;
}

static void logLine(const char* level, const std::string& message) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    char head[48];
    std::snprintf(head, sizeof head, "%04u-%02u-%02u %02u:%02u:%02u %-5s ", t.wYear, t.wMonth, t.wDay, t.wHour,
                  t.wMinute, t.wSecond, level);
    std::string line = head + message + "\r\n";
    HANDLE h = CreateFileW(g_logPath.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(h, line.data(), (DWORD)line.size(), &written, nullptr);
    CloseHandle(h);
}
static void logInfo(const std::string& m) { logLine("INFO", m); }
static void logWarn(const std::string& m) { logLine("WARN", m); }
static void logError(const std::string& m) { logLine("ERROR", m); }

static std::string nowIso() {
    SYSTEMTIME t;
    GetLocalTime(&t);
    char buf[32];
    std::snprintf(buf, sizeof buf, "%04u-%02u-%02uT%02u:%02u:%02u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute,
                  t.wSecond);
    return buf;
}

// ============================================================================
// Preferences (utils/settings.json -> "preferences")
// ============================================================================
struct Prefs {
    bool captureEnabled = true;
    long long maxHistory = 10;
    bool pinsBypassLimit = true;
    std::string duplicateHandling = "move-to-top";  // move-to-top | keep-both | ignore
    bool ignoreWhitespaceOnly = true;
    // Paste shortcuts
    bool hotkeysEnabled = true;
    std::string hotkeyModifiers = "ctrl+alt";       // preset, or "custom"
    std::string customHotkeyModifiers = "ctrl+alt+shift";
    std::string pasteMode = "keep-formatting";      // keep-formatting | text-only
    bool trimWhitespaceOnPaste = false;
    bool pasteAddSpace = false;    // press Space after Ctrl+V
    bool pasteAddNewline = false;  // press Enter after Ctrl+V (after the space, if both)
    bool restoreClipboardAfterPaste = true;

    // The modifiers actually used with 0-9.
    std::string pasteModifiers() const {
        return hotkeyModifiers == "custom" ? customHotkeyModifiers : hotkeyModifiers;
    }
};

static Prefs loadPrefs() {
    Prefs p;
    std::string raw;
    if (!readFile(g_settingsPath, raw)) return p;
    try {
        json::Value doc = json::Parser(raw).parse();
        json::Value* prefs = doc.get("preferences");
        if (!prefs) return p;
        auto boolean = [&](const char* key, bool& out) {
            if (json::Value* v = prefs->get(key); v && v->type == json::Value::Bool) out = v->boolean;
        };
        auto string = [&](const char* key, std::string& out) {
            if (json::Value* v = prefs->get(key); v && v->type == json::Value::String) out = v->text;
        };
        boolean("capture-enabled", p.captureEnabled);
        boolean("pins-bypass-limit", p.pinsBypassLimit);
        boolean("ignore-whitespace-only", p.ignoreWhitespaceOnly);
        boolean("hotkeys-enabled", p.hotkeysEnabled);
        boolean("trim-whitespace-on-paste", p.trimWhitespaceOnPaste);
        boolean("paste-add-space", p.pasteAddSpace);
        boolean("paste-add-newline", p.pasteAddNewline);
        boolean("restore-clipboard-after-paste", p.restoreClipboardAfterPaste);
        if (json::Value* v = prefs->get("max-history-limit"); v && v->type == json::Value::Number)
            p.maxHistory = std::max(0LL, std::atoll(v->text.c_str()));
        string("duplicate-handling", p.duplicateHandling);
        string("hotkey-modifiers", p.hotkeyModifiers);
        string("custom-hotkey-modifiers", p.customHotkeyModifiers);
        string("paste-mode", p.pasteMode);
    } catch (const std::exception& e) {
        logWarn(std::string("settings.json couldn't be read (") + e.what() + "); using default preferences");
    }
    return p;
}

// ============================================================================
// Clipboard
// ============================================================================
struct Copy {
    std::string text, html, rtf, source;
};

static UINT g_cfHtml, g_cfRtf, g_cfExclude, g_cfCanInclude, g_cfHistoryExclude;

static std::string processName(HWND window) {
    if (!window) return {};
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (!pid) return {};
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return {};
    wchar_t buf[MAX_PATH * 2];
    DWORD size = (DWORD)(sizeof buf / sizeof buf[0]);
    std::wstring name;
    if (QueryFullProcessImageNameW(process, 0, buf, &size)) {
        name.assign(buf, size);
        size_t slash = name.find_last_of(L"\\/");
        if (slash != std::wstring::npos) name = name.substr(slash + 1);
    }
    CloseHandle(process);
    return toUtf8(name);
}

static std::string readBytes(UINT format) {
    HANDLE data = GetClipboardData(format);
    if (!data) return {};
    const char* p = (const char*)GlobalLock(data);
    if (!p) return {};
    size_t size = GlobalSize(data);
    size_t len = strnlen(p, size);
    std::string out(p, len);
    GlobalUnlock(data);
    return out;
}

static bool openClipboard(HWND hwnd) {
    for (int attempt = 0; attempt < 20; ++attempt) {  // another app may be holding it briefly
        if (OpenClipboard(hwnd)) return true;
        Sleep(25);
    }
    return false;
}

// Returns false (with a reason) when this copy shouldn't be recorded.
static bool readClipboard(HWND hwnd, Copy& copy, std::string& reason) {
    copy.source = processName(GetClipboardOwner());
    if (copy.source.empty()) copy.source = processName(GetForegroundWindow());

    // Apps like password managers mark their copies as private.
    if (IsClipboardFormatAvailable(g_cfExclude) || IsClipboardFormatAvailable(g_cfHistoryExclude)) {
        reason = "the app that copied it marked it as private (e.g. a password manager)";
        return false;
    }
    if (!IsClipboardFormatAvailable(CF_UNICODETEXT)) {
        reason = "it has no text (e.g. an image or files)";
        return false;
    }
    if (!openClipboard(hwnd)) {
        reason = "the clipboard was locked by another app";
        return false;
    }

    bool ok = true;
    if (IsClipboardFormatAvailable(g_cfCanInclude)) {
        if (HANDLE h = GetClipboardData(g_cfCanInclude)) {
            if (const DWORD* allowed = (const DWORD*)GlobalLock(h)) {
                if (GlobalSize(h) >= sizeof(DWORD) && *allowed == 0) {
                    reason = "the app that copied it excluded it from clipboard history";
                    ok = false;
                }
                GlobalUnlock(h);
            }
        }
    }
    if (ok) {
        HANDLE h = GetClipboardData(CF_UNICODETEXT);
        const wchar_t* w = h ? (const wchar_t*)GlobalLock(h) : nullptr;
        if (!w) {
            reason = "its text couldn't be read";
            ok = false;
        } else {
            size_t len = wcsnlen(w, GlobalSize(h) / sizeof(wchar_t));
            if (len > MAX_TEXT_CHARS) {
                reason = "it's too large (" + std::to_string(len) + " characters)";
                ok = false;
            } else {
                copy.text = toUtf8(w, len);
            }
            GlobalUnlock(h);
        }
    }
    if (ok) {
        if (IsClipboardFormatAvailable(g_cfHtml)) copy.html = readBytes(g_cfHtml);   // CF_HTML is UTF-8
        if (IsClipboardFormatAvailable(g_cfRtf)) copy.rtf = readBytes(g_cfRtf);
        if (!isValidUtf8(copy.html)) copy.html = ansiToUtf8(copy.html);
        if (!isValidUtf8(copy.rtf)) copy.rtf = ansiToUtf8(copy.rtf);
        if (copy.html.size() > MAX_RICH_BYTES) copy.html.clear();
        if (copy.rtf.size() > MAX_RICH_BYTES) copy.rtf.clear();
    }
    CloseClipboard();
    return ok;
}

// ============================================================================
// history.json
// ============================================================================
static std::string newId(const json::Value& history, const json::Value& pinned) {
    static std::mt19937_64 rng([] {
        LARGE_INTEGER t;
        QueryPerformanceCounter(&t);
        return (uint64_t)t.QuadPart ^ ((uint64_t)GetCurrentProcessId() << 32) ^ GetTickCount64();
    }());
    for (;;) {
        char buf[17];
        std::snprintf(buf, sizeof buf, "%016llx", (unsigned long long)rng());
        std::string id(buf, 12);
        bool clash = false;
        for (const json::Value* list : {&history, &pinned})
            for (const json::Value& item : list->items)
                if (item.type == json::Value::Object)
                    if (auto* v = const_cast<json::Value&>(item).get("id"); v && v->text == id) clash = true;
        if (!clash) return id;
    }
}

static bool isBlank(const std::string& s) {
    for (unsigned char c : s)
        if (!(c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f')) return false;
    return true;
}

static std::string describe(const Copy& c) {
    std::string d = std::to_string(MultiByteToWideChar(CP_UTF8, 0, c.text.data(), (int)c.text.size(), nullptr, 0)) +
                    " chars";
    if (!c.html.empty()) d += ", HTML";
    if (!c.rtf.empty()) d += ", RTF";
    if (!c.source.empty()) d += ", from " + c.source;
    return d;
}

struct LockGuard {
    bool held = false;
    LockGuard() {
        if (!g_historyLock) return;
        DWORD r = WaitForSingleObject(g_historyLock, 5000);
        held = (r == WAIT_OBJECT_0 || r == WAIT_ABANDONED);
    }
    ~LockGuard() {
        if (held) ReleaseMutex(g_historyLock);
    }
};

static void recordCopy(const Copy& copy) {
    Prefs prefs = loadPrefs();
    if (!prefs.captureEnabled) return;  // paused from the GUI
    if (copy.text.empty() || (prefs.ignoreWhitespaceOnly && isBlank(copy.text))) {
        logInfo("Ignored a blank copy");
        return;
    }

    LockGuard lock;
    if (!lock.held) logWarn("Couldn't get the history lock; saving anyway");

    json::Value doc = json::Value::object();
    std::string raw;
    bool missing = false;
    if (readFile(g_historyPath, raw, &missing)) {
        try {
            doc = json::Parser(raw).parse();
        } catch (const std::exception& e) {
            // Never overwrite a file we can't understand.
            logError(std::string("history.json is unreadable (") + e.what() + "); this copy was not recorded");
            return;
        }
    } else if (!missing) {
        logError("history.json couldn't be opened; this copy was not recorded");
        return;
    }
    if (doc.type != json::Value::Object) doc = json::Value::object();
    if (!doc.get("schema-version")) doc.set("schema-version", json::Value::number(1));
    for (const char* list : {"history", "pinned"})
        if (!doc.get(list) || doc.get(list)->type != json::Value::Array) doc.set(list, json::Value::array());
    json::Value& history = *doc.get("history");
    json::Value& pinned = *doc.get("pinned");

    // Duplicate handling
    int existing = -1;
    for (size_t i = 0; i < history.items.size(); ++i) {
        json::Value& item = history.items[i];
        if (json::Value* t = item.get("text"); t && t->type == json::Value::String && t->text == copy.text) {
            existing = (int)i;
            break;
        }
    }
    std::string action;
    if (existing >= 0 && prefs.duplicateHandling == "ignore") {
        logInfo("Ignored a copy that's already in History (" + describe(copy) + ")");
        return;
    }
    if (existing >= 0 && prefs.duplicateHandling != "keep-both") {  // move-to-top (default)
        json::Value item = std::move(history.items[(size_t)existing]);
        history.items.erase(history.items.begin() + existing);
        item.set("copied-at", json::Value::string(nowIso()));
        // Keep what we already know (e.g. formatting from the original copy); fill in anything new.
        auto fill = [&](const char* key, const std::string& value) {
            json::Value* v = item.get(key);
            if (!value.empty() && (!v || v->type == json::Value::Null)) item.set(key, json::Value::string(value));
        };
        fill("html", copy.html);
        fill("rtf", copy.rtf);
        fill("source", copy.source);
        history.items.insert(history.items.begin(), std::move(item));
        action = "Moved an existing item to the top";
    } else {
        json::Value item = json::Value::object();
        item.set("id", json::Value::string(newId(history, pinned)));
        item.set("text", json::Value::string(copy.text));
        item.set("html", json::Value::stringOrNull(copy.html));
        item.set("rtf", json::Value::stringOrNull(copy.rtf));
        item.set("copied-at", json::Value::string(nowIso()));
        item.set("source", json::Value::stringOrNull(copy.source));
        history.items.insert(history.items.begin(), std::move(item));
        action = "Recorded a copy";
    }

    // History limit
    long long limit = prefs.pinsBypassLimit ? prefs.maxHistory
                                            : std::max(0LL, prefs.maxHistory - (long long)pinned.items.size());
    size_t trimmed = 0;
    while ((long long)history.items.size() > limit) {
        history.items.pop_back();
        ++trimmed;
    }

    std::string out;
    json::write(out, doc);
    out += "\n";
    if (!writeFileAtomic(g_historyPath, out)) {
        logError("Couldn't save history.json (error " + std::to_string(GetLastError()) + ")");
        return;
    }
    logInfo(action + " (" + describe(copy) + ")" +
            (trimmed ? "; removed " + std::to_string(trimmed) + " old item(s)" : ""));
}

// ============================================================================
// Paste shortcuts
// ============================================================================
static Prefs g_prefs;                     // last loaded preferences
static std::string g_activeModifiers;     // modifiers the 0-9 hotkeys are registered with ("" = none)
static std::vector<std::string> g_registered, g_failed;  // digits
static std::vector<DWORD> g_ownSequences; // clipboard changes made by us (not recorded as copies)

static UINT modifierFlags(const std::string& spec, bool& valid) {
    UINT flags = 0;
    size_t start = 0;
    while (start <= spec.size()) {
        size_t plus = spec.find('+', start);
        std::string token = spec.substr(start, plus == std::string::npos ? std::string::npos : plus - start);
        if (token == "ctrl") flags |= MOD_CONTROL;
        else if (token == "alt") flags |= MOD_ALT;
        else if (token == "shift") flags |= MOD_SHIFT;
        else if (token == "win") flags |= MOD_WIN;
        if (plus == std::string::npos) break;
        start = plus + 1;
    }
    valid = (flags & (MOD_CONTROL | MOD_ALT | MOD_WIN)) != 0;  // Shift alone would just type symbols
    return flags;
}

static std::string modifierLabel(const std::string& spec) {
    static const std::pair<const char*, const char*> names[] = {
        {"ctrl", "Ctrl"}, {"alt", "Alt"}, {"shift", "Shift"}, {"win", "Win"}};
    std::string out;
    for (auto& [token, label] : names) {
        std::string padded = "+" + spec + "+";
        if (padded.find(std::string("+") + token + "+") != std::string::npos) {
            if (!out.empty()) out += "+";
            out += label;
        }
    }
    return out;
}

static void unregisterPasteHotkeys(HWND hwnd) {
    for (int d = 0; d < 10; ++d) UnregisterHotKey(hwnd, HOTKEY_BASE + d);
    g_activeModifiers.clear();
    g_registered.clear();
    g_failed.clear();
}

static void registerPasteHotkeys(HWND hwnd) {
    unregisterPasteHotkeys(hwnd);
    if (!g_prefs.hotkeysEnabled) {
        logInfo("Paste shortcuts are turned off");
        return;
    }
    const std::string spec = g_prefs.pasteModifiers();
    bool valid = false;
    UINT flags = modifierFlags(spec, valid);
    if (!valid) {
        logWarn("Paste shortcut keys \"" + spec + "\" aren't valid (need Ctrl, Alt or Win); shortcuts are off");
        return;
    }
    g_activeModifiers = spec;
    for (int d = 0; d < 10; ++d) {
        std::string digit(1, (char)('0' + d));
        if (RegisterHotKey(hwnd, HOTKEY_BASE + d, flags | MOD_NOREPEAT, '0' + d)) g_registered.push_back(digit);
        else g_failed.push_back(digit);
    }
    const std::string label = modifierLabel(spec);
    if (g_failed.empty()) {
        logInfo("Paste shortcuts active: " + label + "+0-9");
    } else {
        std::string list;
        for (auto& d : g_failed) list += (list.empty() ? "" : ", ") + label + "+" + d;
        logWarn("Paste shortcuts active: " + label + "+0-9, except " + list +
                " (already used by Windows or another app)");
    }
}

// --- clipboard save / restore ------------------------------------------------
struct SavedFormat {
    UINT format;
    std::vector<char> data;
};
static std::vector<SavedFormat> g_savedClipboard;

static bool isHandleFormat(UINT f) {  // formats that hold GDI handles, not memory we can copy
    return f == CF_BITMAP || f == CF_METAFILEPICT || f == CF_PALETTE || f == CF_ENHMETAFILE ||
           f == CF_OWNERDISPLAY || f == CF_DSPBITMAP || f == CF_DSPMETAFILEPICT || f == CF_DSPENHMETAFILE ||
           (f >= CF_GDIOBJFIRST && f <= CF_GDIOBJLAST);
}

static void saveClipboard(HWND hwnd) {
    g_savedClipboard.clear();
    if (!openClipboard(hwnd)) return;
    for (UINT f = EnumClipboardFormats(0); f; f = EnumClipboardFormats(f)) {
        if (isHandleFormat(f)) continue;
        HANDLE h = GetClipboardData(f);
        if (!h) continue;
        SIZE_T size = GlobalSize(h);
        if (size == 0 || size > 64u * 1024 * 1024) continue;
        const char* p = (const char*)GlobalLock(h);
        if (!p) continue;
        g_savedClipboard.push_back({f, std::vector<char>(p, p + size)});
        GlobalUnlock(h);
    }
    CloseClipboard();
}

static bool putClipboardData(UINT format, const void* data, size_t size) {
    HGLOBAL h = GlobalAlloc(GMEM_MOVEABLE, size);
    if (!h) return false;
    void* p = GlobalLock(h);
    std::memcpy(p, data, size);
    GlobalUnlock(h);
    if (!SetClipboardData(format, h)) {
        GlobalFree(h);
        return false;
    }
    return true;
}

static void rememberOwnChange() {
    g_ownSequences.push_back(GetClipboardSequenceNumber());
    if (g_ownSequences.size() > 16) g_ownSequences.erase(g_ownSequences.begin());
}

static void restoreClipboard(HWND hwnd) {
    if (!openClipboard(hwnd)) {
        logWarn("Couldn't restore the previous clipboard (it was locked by another app)");
        return;
    }
    EmptyClipboard();
    for (auto& f : g_savedClipboard) putClipboardData(f.format, f.data.data(), f.data.size());
    CloseClipboard();
    rememberOwnChange();
    g_savedClipboard.clear();
}

// --- sending keys ------------------------------------------------------------
static void addKey(std::vector<INPUT>& inputs, WORD vk, bool up) {
    INPUT in{};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = vk;
    in.ki.wScan = (WORD)MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
    in.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
    if (vk == VK_RWIN || vk == VK_LWIN || vk == VK_RMENU || vk == VK_RCONTROL) in.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
    inputs.push_back(in);
}

// An unassigned key press "uses up" a held Alt/Win, so letting go of it doesn't open
// the app's menu bar or the Start menu.
static void addMaskKey(std::vector<INPUT>& inputs) {
    addKey(inputs, 0xE8, false);
    addKey(inputs, 0xE8, true);
}

static bool isDown(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }
static const int MODIFIER_KEYS[] = {VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LSHIFT, VK_RSHIFT, VK_LWIN, VK_RWIN};
static bool anyModifierDown() {
    for (int vk : MODIFIER_KEYS)
        if (isDown(vk)) return true;
    return false;
}

static void sendPasteKeys() {
    std::vector<INPUT> inputs;
    if (anyModifierDown()) {  // the user is still holding the shortcut: let go of it for them
        addMaskKey(inputs);
        for (int vk : MODIFIER_KEYS)
            if (isDown(vk)) addKey(inputs, (WORD)vk, true);
    }
    addKey(inputs, VK_CONTROL, false);
    addKey(inputs, 'V', false);
    addKey(inputs, 'V', true);
    addKey(inputs, VK_CONTROL, true);
    // Extra keys are typed after the paste in the same input batch, so they arrive in order.
    if (g_prefs.pasteAddSpace) {
        addKey(inputs, VK_SPACE, false);
        addKey(inputs, VK_SPACE, true);
    }
    if (g_prefs.pasteAddNewline) {
        addKey(inputs, VK_RETURN, false);
        addKey(inputs, VK_RETURN, true);
    }
    if (SendInput((UINT)inputs.size(), inputs.data(), sizeof(INPUT)) != inputs.size())
        logWarn("Couldn't send Ctrl+V (the focused app may be running as administrator)");
}

// --- the paste itself --------------------------------------------------------
struct PendingPaste {
    bool active = false;
    bool restore = false;
    ULONGLONG started = 0;
};
static PendingPaste g_paste;

static std::string trimmed(const std::string& s) {
    const char* ws = " \t\r\n\v\f";
    size_t a = s.find_first_not_of(ws);
    if (a == std::string::npos) return {};
    size_t b = s.find_last_not_of(ws);
    return s.substr(a, b - a + 1);
}

static void startPaste(HWND hwnd, int digit) {
    if (g_paste.active) return;  // previous paste still finishing
    const std::string label = modifierLabel(g_activeModifiers) + "+" + std::to_string(digit);

    std::string raw;
    json::Value doc;
    try {
        if (!readFile(g_historyPath, raw)) throw std::runtime_error("couldn't open history.json");
        doc = json::Parser(raw).parse();
    } catch (const std::exception& e) {
        logError(label + ": " + e.what());
        return;
    }
    json::Value* history = doc.get("history");
    if (!history || history->type != json::Value::Array || (size_t)digit >= history->items.size()) {
        logInfo(label + ": there's no History item " + std::to_string(digit));
        MessageBeep(MB_OK);
        return;
    }
    json::Value& item = history->items[(size_t)digit];
    auto field = [&](const char* key) -> std::string {
        json::Value* v = item.get(key);
        return v && v->type == json::Value::String ? v->text : std::string();
    };
    std::string text = field("text");
    std::string html = g_prefs.pasteMode == "text-only" ? "" : field("html");
    std::string rtf = g_prefs.pasteMode == "text-only" ? "" : field("rtf");
    if (g_prefs.trimWhitespaceOnPaste) text = trimmed(text);
    if (text.empty() && html.empty() && rtf.empty()) {
        logInfo(label + ": History item " + std::to_string(digit) + " is empty; nothing pasted");
        return;
    }

    // Let go of Alt/Win cleanly before anything else happens.
    std::vector<INPUT> mask;
    addMaskKey(mask);
    SendInput((UINT)mask.size(), mask.data(), sizeof(INPUT));

    if (g_prefs.restoreClipboardAfterPaste) saveClipboard(hwnd);
    if (!openClipboard(hwnd)) {
        logWarn(label + ": the clipboard is locked by another app; nothing pasted");
        return;
    }
    EmptyClipboard();
    int wlen = MultiByteToWideChar(CP_UTF8, 0, text.data(), (int)text.size(), nullptr, 0);
    std::wstring wide((size_t)wlen + 1, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), (int)text.size(), &wide[0], wlen);
    putClipboardData(CF_UNICODETEXT, wide.data(), wide.size() * sizeof(wchar_t));
    if (!html.empty()) putClipboardData(g_cfHtml, html.c_str(), html.size() + 1);
    if (!rtf.empty()) putClipboardData(g_cfRtf, rtf.c_str(), rtf.size() + 1);
    CloseClipboard();
    rememberOwnChange();  // don't record our own clipboard change as a new copy

    g_paste = {true, g_prefs.restoreClipboardAfterPaste, GetTickCount64()};
    SetTimer(hwnd, TIMER_PASTE, 15, nullptr);
    logInfo(label + ": pasting History item " + std::to_string(digit) + " (" +
            (html.empty() && rtf.empty() ? "plain text" : "with formatting") + ")");
}

static void continuePaste(HWND hwnd) {
    // Wait (briefly) for the user to release the shortcut keys, so the app gets a clean Ctrl+V.
    if (anyModifierDown() && GetTickCount64() - g_paste.started < MODIFIER_WAIT_MS) return;
    KillTimer(hwnd, TIMER_PASTE);
    sendPasteKeys();
    if (g_paste.restore) SetTimer(hwnd, TIMER_RESTORE, RESTORE_DELAY_MS, nullptr);
    else g_paste.active = false;
}

static void finishPaste(HWND hwnd) {
    KillTimer(hwnd, TIMER_RESTORE);
    restoreClipboard(hwnd);
    g_paste.active = false;
}

// ============================================================================
// Settings reload + status file
// ============================================================================
static FILETIME g_settingsStamp{};

static FILETIME settingsStamp() {
    WIN32_FILE_ATTRIBUTE_DATA info{};
    if (!GetFileAttributesExW(g_settingsPath.c_str(), GetFileExInfoStandard, &info)) return FILETIME{};
    return info.ftLastWriteTime;
}

static void writeStatus(unsigned long long refreshId) {
    json::Value shortcuts = json::Value::object();
    json::Value enabled;
    enabled.type = json::Value::Bool;
    enabled.boolean = g_prefs.hotkeysEnabled && !g_activeModifiers.empty();
    shortcuts.set("enabled", enabled);
    shortcuts.set("modifiers", json::Value::string(g_activeModifiers));
    json::Value registered = json::Value::array(), failed = json::Value::array();
    for (auto& d : g_registered) registered.items.push_back(json::Value::string(d));
    for (auto& d : g_failed) failed.items.push_back(json::Value::string(d));
    shortcuts.set("registered", registered);
    shortcuts.set("failed", failed);

    json::Value capture;
    capture.type = json::Value::Bool;
    capture.boolean = g_prefs.captureEnabled;

    json::Value status = json::Value::object();
    status.set("schema-version", json::Value::number(1));
    status.set("pid", json::Value::number(GetCurrentProcessId()));
    status.set("updated-at", json::Value::string(nowIso()));
    status.set("refresh-id", json::Value::number((long long)refreshId));
    status.set("capture-enabled", capture);
    status.set("paste-shortcuts", shortcuts);
    std::string out;
    json::write(out, status);
    out += "\n";
    if (!writeFileAtomic(g_statusPath, out)) logWarn("Couldn't write the status file");
}

static void reloadSettings(HWND hwnd, unsigned long long refreshId, const char* why) {
    g_settingsStamp = settingsStamp();
    g_prefs = loadPrefs();
    logInfo(std::string("Settings reloaded (") + why + ")");
    // Re-register when the shortcut keys changed, or on an explicit refresh (which also retries keys
    // that were taken last time, e.g. by an app that has since closed).
    const std::string wanted = g_prefs.hotkeysEnabled ? g_prefs.pasteModifiers() : std::string();
    if (refreshId || wanted != g_activeModifiers) registerPasteHotkeys(hwnd);
    writeStatus(refreshId);
}

static void checkSettingsChanged(HWND hwnd) {
    FILETIME now = settingsStamp();
    if (CompareFileTime(&now, &g_settingsStamp) != 0) reloadSettings(hwnd, 0, "settings.json changed");
}

// ============================================================================
// Window + message loop
// ============================================================================
static DWORD g_lastSequence = 0;

static void onClipboardChanged(HWND hwnd) {
    DWORD seq = GetClipboardSequenceNumber();
    if (seq == g_lastSequence) return;
    g_lastSequence = seq;
    for (DWORD own : g_ownSequences)
        if (own == seq) return;  // our own paste/restore, not a copy
    Copy copy;
    std::string reason;
    if (!readClipboard(hwnd, copy, reason)) {
        logInfo("Skipped a copy: " + reason);
        return;
    }
    try {
        recordCopy(copy);
    } catch (const std::exception& e) {
        logError(std::string("Failed to record a copy: ") + e.what());
    }
}

static LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CLIPBOARDUPDATE:
            SetTimer(hwnd, TIMER_CLIPBOARD, DEBOUNCE_MS, nullptr);  // wait for the app to finish setting all formats
            return 0;
        case WM_HOTKEY:
            if ((int)wp >= HOTKEY_BASE && (int)wp < HOTKEY_BASE + 10) startPaste(hwnd, (int)wp - HOTKEY_BASE);
            return 0;
        case WM_APP_REFRESH:
            reloadSettings(hwnd, (unsigned long long)wp, "refresh requested by CopyManager");
            return 0;
        case WM_TIMER:
            switch (wp) {
                case TIMER_CLIPBOARD:
                    KillTimer(hwnd, TIMER_CLIPBOARD);
                    onClipboardChanged(hwnd);
                    break;
                case TIMER_SETTINGS: checkSettingsChanged(hwnd); break;
                case TIMER_PASTE: continuePaste(hwnd); break;
                case TIMER_RESTORE: finishPaste(hwnd); break;
            }
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static std::wstring parentDir(const std::wstring& path) {
    size_t slash = path.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"." : path.substr(0, slash);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    // Default paths: <root>\bin\monitor.exe -> <root>\utils\settings.json etc.
    wchar_t exe[MAX_PATH * 2];
    GetModuleFileNameW(nullptr, exe, (DWORD)(sizeof exe / sizeof exe[0]));
    const std::wstring root = parentDir(parentDir(exe));
    g_settingsPath = root + L"\\utils\\settings.json";
    g_historyPath = root + L"\\src\\history.json";
    g_logPath = root + L"\\logs\\monitor.log";
    g_statusPath = root + L"\\logs\\monitor-status.json";
    DWORD parentPid = 0;

    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; argv && i + 1 < argc; i += 2) {
        std::wstring flag = argv[i], value = argv[i + 1];
        if (flag == L"--settings") g_settingsPath = value;
        else if (flag == L"--history") g_historyPath = value;
        else if (flag == L"--log") g_logPath = value;
        else if (flag == L"--status") g_statusPath = value;
        else if (flag == L"--parent-pid") parentPid = (DWORD)std::wcstoul(value.c_str(), nullptr, 10);
    }
    if (argv) LocalFree(argv);

    CreateDirectoryW(parentDir(g_logPath).c_str(), nullptr);
    WIN32_FILE_ATTRIBUTE_DATA info;  // keep the log from growing forever
    if (GetFileAttributesExW(g_logPath.c_str(), GetFileExInfoStandard, &info) && info.nFileSizeLow > 1'000'000)
        MoveFileExW(g_logPath.c_str(), (g_logPath + L".1").c_str(), MOVEFILE_REPLACE_EXISTING);

    HANDLE single = CreateMutexW(nullptr, FALSE, L"Local\\CopyManager.Monitor");
    // "Access denied" (no handle) also means one exists: it belongs to an administrator monitor.
    if (!single || GetLastError() == ERROR_ALREADY_EXISTS) {
        logInfo("Another monitor is already running; this one is exiting");
        return 0;
    }
    g_historyLock = CreateMutexW(nullptr, FALSE, L"Local\\CopyManager.HistoryLock");

    g_cfHtml = RegisterClipboardFormatW(L"HTML Format");
    g_cfRtf = RegisterClipboardFormatW(L"Rich Text Format");
    g_cfExclude = RegisterClipboardFormatW(L"ExcludeClipboardContentFromMonitorProcessing");
    g_cfHistoryExclude = RegisterClipboardFormatW(L"Clipboard Viewer Ignore");
    g_cfCanInclude = RegisterClipboardFormatW(L"CanIncludeInClipboardHistory");

    WNDCLASSW wc{};
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.lpszClassName = L"CopyManagerMonitor";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"CopyManager Monitor", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr,
                                instance, nullptr);
    if (!hwnd || !AddClipboardFormatListener(hwnd)) {
        logError("Couldn't start listening to the clipboard (error " + std::to_string(GetLastError()) + ")");
        return 1;
    }

    HANDLE parent = nullptr;
    if (parentPid) {
        parent = OpenProcess(SYNCHRONIZE, FALSE, parentPid);
        if (!parent) {
            logInfo("CopyManager isn't running any more; monitor exiting");
            return 0;
        }
    }
    g_lastSequence = GetClipboardSequenceNumber();  // only record copies made from now on
    logInfo("Monitor started; watching the clipboard");
    reloadSettings(hwnd, 0, "startup");             // registers the paste shortcuts, writes the status file
    SetTimer(hwnd, TIMER_SETTINGS, SETTINGS_CHECK_MS, nullptr);

    for (;;) {
        DWORD r = MsgWaitForMultipleObjects(parent ? 1 : 0, parent ? &parent : nullptr, FALSE, INFINITE, QS_ALLINPUT);
        if (parent && r == WAIT_OBJECT_0) {
            logInfo("CopyManager closed; monitor stopping");
            break;
        }
        MSG msg;
        bool quit = false;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) { quit = true; break; }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (quit) break;
    }

    if (g_paste.active && g_paste.restore) restoreClipboard(hwnd);  // don't leave a half-finished paste behind
    unregisterPasteHotkeys(hwnd);
    RemoveClipboardFormatListener(hwnd);
    DestroyWindow(hwnd);
    if (parent) CloseHandle(parent);
    CloseHandle(single);
    return 0;
}
