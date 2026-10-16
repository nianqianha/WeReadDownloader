# -*- coding: utf-8 -*-
"""First-run credential wizard.

WHY THIS EXISTS
    A fresh install has no credential file, and load_credentials() raises
    "Credential file not found" before the window can be useful. Asking a new
    user to hand-craft a Lua table is not a reasonable first experience, so the
    wizard collects the two values a person can actually obtain from the web
    page and writes the file for them.

THE TWO VALUES
    api_key   (wrk-...)  from https://weread.qq.com/r/weread-skills
    (nothing else -- the QR login fills in vid and wr_skey itself)

    wr_skey is NOT collected here: it only exists after a scan-login, which the
    main window performs. Until that happens the file is written with an empty
    cookies table, and load_credentials() keeps reporting that a session is
    still needed -- which is the truth, and the main window says so.

FILE FORMAT
    weread_desktop.load_credentials() parses the file by finding "\\nreturn {"
    and running the remainder through LuaJIT, so the body must be a valid Lua
    table literal. It requires api_key to be non-empty and cookies.wr_skey to be
    non-empty, hence the explicit checks below.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

# Shown on the first screen; the URL is where both values come from.
SKILLS_URL = "https://weread.qq.com/r/weread-skills"


def _lua_quote(value: str) -> str:
    """Escape a Python string for a double-quoted Lua literal."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return "".join(out)


def build_settings_lua(api_key: str,
                       cookies: dict | None = None,
                       wr_ticket: str = "",
                       wr_wrpa: str = "") -> str:
    """Render the credential file exactly as load_credentials() expects it."""
    lines = [
        "-- Written by the first-run wizard. Edited by hand only if you know",
        "-- what load_credentials() in weread_desktop.py requires:",
        "--   api_key must be non-empty, and cookies.wr_skey must be non-empty",
        "--   before a download can start.",
        "return {",
        '  api_key = "%s",' % _lua_quote(api_key),
        "  cookies = {",
    ]
    for key, value in (cookies or {}).items():
        lines.append('    %s = "%s",' % (key, _lua_quote(str(value))))
    lines += [
        "  },",
        '  wr_ticket = "%s",' % _lua_quote(wr_ticket),
        '  wr_wrpa = "%s",' % _lua_quote(wr_wrpa),
        "}",
        "",
    ]
    return "\n".join(lines)


def validate(api_key: str) -> str:
    """Return an error message, or "" when both values look usable.

    Deliberately strict about shape and silent about correctness: whether the
    key is *valid* can only be answered by the server, which the caller does
    next. Catching a typo here saves a round trip and a confusing error.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return "请填写 api_key。"
    if not api_key.startswith("wrk-"):
        return "api_key 应以 wrk- 开头，请检查是否复制完整。"
    if len(api_key) < 12:
        return "api_key 看起来太短了，请确认复制完整。"
    return ""


def _validate_creds(api_key: str, skey: str) -> str:
    """Validate an inline-card submission.

    Unlike validate(), wr_skey is optional here: the QR login fills it, so a
    user who only has api_key must be able to save and then scan. Returns an
    error message, or "" when the values are acceptable.
    """
    api_key = (api_key or "").strip()
    skey = (skey or "").strip()
    if not api_key:
        return "请填写 API Key。"
    if not api_key.startswith("wrk-"):
        return "API Key 应以 wrk- 开头，请检查是否复制完整。"
    if len(api_key) < 12:
        return "API Key 看起来太短了，请确认复制完整。"
    return ""


def _build_settings_lua(api_key: str, skey: str = "",
                        cookies: dict | None = None) -> str:
    """Render the credential file, with or without a session.

    cookies is preferred when given (it can carry wr_vid and the other session
    keys the QR login produces); skey alone is the hand-entered fallback.
    """
    jar = dict(cookies or {})
    if skey and not jar:
        jar["wr_skey"] = skey
    return build_settings_lua(api_key, cookies=jar)


def write_settings(settings_path: Path, api_key: str,
                   cookies: dict | None = None) -> Path:
    """Write the credential file, creating the parent directory if needed."""
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    text = build_settings_lua(api_key.strip(), cookies=cookies)
    temp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, settings_path)
    return settings_path


def _settings_path() -> Path:
    """Where the credential file lives.

    weread_core owns the profile directory (PRIVATE) and is present in every
    bundle. weread_desktop also defines SETTINGS, but the GUI bundle EXCLUDES
    that module on purpose -- it drags in LuaJIT and lxml, which cost ~5 s of
    startup -- so importing from it raised ImportError inside the frozen GUI.
    main() catches that and lets the window through, which is why the wizard
    silently never appeared while the main window opened normally.

    Resolution order: weread_core first (always present), then weread_desktop
    for the source tree, then a literal fallback so the wizard still works if
    neither can be imported.
    """
    for module in ("weread_core", "weread_desktop"):
        try:
            try:
                mod = __import__("." + module, fromlist=["*"])
            except ImportError:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                mod = __import__(module, fromlist=["*"])
        except ImportError:
            continue
        settings = getattr(mod, "SETTINGS", None)
        if settings is not None:
            return Path(settings)
        private = getattr(mod, "PRIVATE", None)
        if private is not None:
            return Path(private) / "weread-live-settings.lua"
    # Last resort: the same location weread_core would compute.
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    return Path(base) / "WeReadDesktop" / "weread-live-settings.lua"


def _settings_literal(text: str) -> dict:
    """Read the data-table format written by build_settings_lua without Lua."""
    token_re = re.compile(r"[ \t\r\n\f\v]+|--[^\r\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[A-Za-z_][A-Za-z_0-9]*|[{}\[\]=,;]")
    tokens = []
    pos = 0
    while pos < len(text):
        match = token_re.match(text, pos)
        if match is None:
            raise ValueError("Unsupported settings syntax")
        token = match.group()
        pos = match.end()
        if not token.isspace() and not token.startswith("--"):
            tokens.append(token)
    index = 0

    def take(expected=None):
        nonlocal index
        if index >= len(tokens) or (expected is not None and tokens[index] != expected):
            raise ValueError("Malformed settings table")
        token = tokens[index]
        index += 1
        return token

    def string(token):
        # Match the writer's escapes; do not silently reinterpret Lua decimal escapes as Python octal.
        if token[:1] not in ("\"", "'") or any(
                match.group(1) not in "\\'\"abfnrtv" for match in re.finditer(r"\\(.)", token)):
            raise ValueError("Unsupported settings string")
        return ast.literal_eval(token)

    def table(depth=0):
        if depth > 16:
            raise ValueError("Settings table is too deeply nested")
        take("{")
        result = {}
        while index < len(tokens) and tokens[index] != "}":
            key = take()
            if key == "[":
                key = string(take())
                take("]")
            elif not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
                raise ValueError("Unsupported settings key")
            take("=")
            if index < len(tokens) and tokens[index] == "{":
                value = table(depth + 1)
            else:
                value = string(take())
            result[key] = value
            if index < len(tokens) and tokens[index] in (",", ";"):
                take()
            elif index >= len(tokens) or tokens[index] != "}":
                raise ValueError("Missing settings separator")
        take("}")
        return result

    result = table()
    if index != len(tokens):
        raise ValueError("Unexpected text after settings table")
    return result


def _read_base_settings(settings: Path) -> dict:
    try:
        raw = settings.read_bytes()
        start = raw.find(b"\nreturn {")
        if start < 0:
            return {}
        try:
            from lupa.luajit21 import LuaRuntime
        except ImportError:
            # shortcut: GUI fallback accepts data tables, not executable Lua expressions.
            values = _settings_literal(raw[start + len(b"\nreturn "):].decode("utf-8"))
        else:
            lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
            table = lua.execute(raw[start + 1:])
            values = {"api_key": (table[b"api_key"] or b"").decode("utf-8"),
                      "cookies": {k.decode("utf-8"): v.decode("utf-8")
                                  for k, v in table[b"cookies"].items()}}
        if not isinstance(values.get("api_key"), str) or not isinstance(values.get("cookies"), dict):
            return {}
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in values["cookies"].items()):
            return {}
        return values
    except Exception:
        return {}


def _credentials_usable(settings: Path) -> bool:
    """Require the base key and apply the worker's overlay-cookie precedence."""
    base = _read_base_settings(settings)
    if not base.get("api_key"):
        return False
    cookies = dict(base["cookies"])
    try:
        try:
            from . import weread_login
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import weread_login
        cookies.update(weread_login.load_saved_credentials().get("cookies") or {})
    except Exception:
        return bool(base["cookies"].get("wr_skey"))
    return bool(cookies.get("wr_skey"))


def _base_file_shape_ok(settings: Path) -> bool:
    base = _read_base_settings(settings)
    return bool(base.get("api_key") and base.get("cookies", {}).get("wr_skey"))


def _has_api_key(settings: Path) -> bool:
    return bool(_read_base_settings(settings).get("api_key"))


def _ensure_credentials_file(root) -> bool:
    """Open the wizard when the credentials are missing or unusable.

    Returns True when a usable file exists on return (it already did, or the
    user just created one), False when the user closed the wizard without
    providing one. Callers use the False to decide whether starting the main
    window makes sense at all.

    NOTE FOR THE READER: "credentials missing" is not "first run". This runs on
    every launch until a usable file exists -- including after a full logout,
    which is the state most users reach first.
    """
    settings = _settings_path()

    if _credentials_usable(settings):
        return True

    # Three states must be told apart, because they need different actions:
    #
    #   nothing at all        -> the wizard: fill in api_key
    #   broken file           -> the wizard: the file is unusable, say so
    #   api_key OK, no wr_skey -> NOT the wizard: the user already did their
    #                            part and the remaining step is the QR login,
    #                            which lives in the main window. Re-showing the
    #                            wizard here bounces them in a loop with no
    #                            explanation, which is exactly what happened:
    #                            the file is written, the dialog returns, the
    #                            check still fails, the dialog appears again.
    #
    # The third state returns True so the main window opens and the user can
    # reach the QR login that completes the credential.
    if _has_api_key(settings):
        return True

    # The file exists but cannot be used. Saying so is worth one dialog: the
    # alternative is a window where every button fails with a credential error,
    # and the only fix is deleting an invisible file.
    unusable = settings.is_file()

    # The dialog's type scale comes from desktop_app when it is importable; a
    # tuple is an acceptable fallback and keeps the wizard usable even if that
    # module cannot be loaded. The signature mirrors desktop_app._font so a call
    # site cannot work with one and break with the other.
    try:
        from .desktop_app import _font
    except ImportError:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from desktop_app import _font
        except ImportError:
            def _font(size, weight="normal"):
                return ("Microsoft YaHei UI", size, weight)

    import customtkinter as ctk
    import webbrowser

    result = {"ok": False}

    dialog = ctk.CTkToplevel(root)
    dialog.title("首次运行 · 配置微信读书凭证")
    dialog.resizable(False, False)
    dialog.transient(root)

    width, height = 560, 470
    dialog.update_idletasks()
    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    dialog.geometry("%dx%d+%d+%d" % (
        width, height, (screen_w - width) // 2,
        max(0, (screen_h - height) // 2 - 20)))

    # A wizard that can be dismissed by accident on first run is worse than no
    # wizard: the window behind it cannot work without the file. Closing it is
    # therefore the explicit "quit" path, which returns False.
    def on_close():
        result["ok"] = False
        dialog.grab_release()
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", on_close)

    if unusable:
        heading_text = "凭证文件无法读取，需要重新填写"
        body_text = (
            "本机的凭证文件是坏的，程序没法用它登录。\n\n"
            "请重新填写下面两个值。如果之前填错过，直接覆盖就好。"
        )
    else:
        heading_text = "首次运行：需要两个凭证"
        body_text = (
            "本程序需要一个微信读书账号。请先确认你已能用手机 App 或\n"
            "浏览器登录 https://weread.qq.com 。\n\n"
            "然后打开下面这个页面，登录后即可看到两个值：\n"
            "    " + SKILLS_URL
        )

    ctk.CTkLabel(dialog, text=heading_text,
                 font=_font(16, "bold"), anchor="w").pack(
        fill="x", padx=24, pady=(20, 6))

    ctk.CTkLabel(dialog, text=body_text, font=_font(12), anchor="w",
                 justify="left", wraplength=500).pack(fill="x", padx=24)

    # _font's second parameter is the font WEIGHT, not a style: passing
    # "underline" made CTkFont reject the value and the dialog died before it
    # was shown. The link is marked by colour and cursor instead, which is what
    # a link needs anyway.
    link = ctk.CTkLabel(dialog, text="→ 点这里打开该页面（也可手动复制上面的网址）",
                        text_color="#4A9EFF", cursor="hand2",
                        font=_font(12), anchor="w")
    link.pack(fill="x", padx=24, pady=(6, 14))
    link.bind("<Button-1>", lambda _e: webbrowser.open(SKILLS_URL))

    ctk.CTkLabel(dialog, text="api_key（以 wrk- 开头）", font=_font(12),
                 anchor="w").pack(fill="x", padx=24)
    api_key_entry = ctk.CTkEntry(dialog, width=500, height=36,
                                 placeholder_text="wrk-xxxxxxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=24, pady=(4, 12))


    status = ctk.CTkLabel(dialog, text="", font=_font(11), anchor="w",
                          text_color="#D9534F", justify="left", wraplength=500)
    status.pack(fill="x", padx=24)

    def submit():
        api_key = api_key_entry.get().strip()
        problem = validate(api_key)
        if problem:
            status.configure(text=problem)
            return
        try:
            write_settings(settings, api_key)
        except OSError as exc:
            status.configure(text="无法写入凭证文件：%s" % exc)
            return
        result["ok"] = True
        dialog.grab_release()
        dialog.destroy()

    buttons = ctk.CTkFrame(dialog, fg_color="transparent")
    buttons.pack(fill="x", padx=24, pady=(10, 18))
    ctk.CTkButton(buttons, text="保存并继续", width=130, height=36,
                  command=submit).pack(side="right")
    ctk.CTkButton(buttons, text="退出程序", width=110, height=36,
                  fg_color="transparent", border_width=1,
                  text_color=("#333333", "#DDDDDD"),
                  command=on_close).pack(side="right", padx=(0, 10))

    # Enter submits from either field; the wizard is two fields, not a form.
    for entry in (api_key_entry,):
        entry.bind("<Return>", lambda _e: submit())
    api_key_entry.focus_set()

    dialog.grab_set()
    root.wait_window(dialog)
    return result["ok"]
