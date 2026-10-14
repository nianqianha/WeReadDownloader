def _ensure_credentials_file(root: "ctk.CTk") -> bool:
    """If the credentials file is missing, prompt the user to create it.
    Returns True if the file now exists (either already existed or was just created),
    False if the user cancelled.
    """
    if SETTINGS.is_file():
        return True

    # First-run dialog
    dialog = ctk.CTkToplevel(None)
    dialog.title("首次运行 - 配置微信读书凭证")
    dialog.geometry("520x380")
    dialog.resizable(False, False)
    dialog.transient(None)
    dialog.grab_set()
    dialog.protocol("WM_DELETE_WINDOW", lambda: None)

    # Center the dialog
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - 520) // 2
    y = (dialog.winfo_screenheight() - 380) // 2
    dialog.geometry(f"520x380+{x}+{y}")

    import customtkinter as ctk
    ctk.CTkLabel(
        None,
        text="首次运行：配置微信读书凭证",
        font=("Microsoft YaHei UI", 14, "bold"),
        text_color="white",
    ).pack(pady=(20, 8))

    ctk.CTkLabel(
        None,
        text=(
            "程序需要两个微信读书的凭证才能工作：\n\n"
            "1. api_key (wrk-...) 和 user_vid\n"
            "   -> 浏览器打开 https://weread.qq.com/r/weread-skills 登录后复制\n\n"
            "2. 扫码登录后会自动获取 wr_skey (cookie)\n"
            "   这一步在程序里点\"扫码登录\"完成"
        ),
        justify="left",
        wraplength=460,
        justify="left",
    ).pack(padx=20, pady=(0, 16), anchor="w")

    # api_key input
    import customtkinter as ctk
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # api_key input
    import customtkinter as ctk2
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # api_key input
    import customtkinter as ctk2
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # api_key input
    import customtkinter as ctk2
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # api_key input
    import customtkinter as ctk2
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # api_key input
    import customtkinter as ctk2
    ctk.CTkLabel(None, text="api_key (wrk-...)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20)
    api_key_var = ctk.StringVar()
    api_key_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="wrk-xxxxxxxxxxxxxxxx")
    api_key_entry.pack(padx=20, pady=(0, 12), fill="x")

    # user_vid input
    ctk.CTkLabel(None, text="user_vid (纯数字)", font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=20, pady=(12, 0))
    user_vid_var = ctk.StringVar()
    user_vid_entry = ctk.CTkEntry(None, textvariable=ctk.StringVar(), width=440, height=36, placeholder_text="纯数字，例如 123456789")
    user_vid_entry.pack(padx=20, pady=(0, 20), fill="x")

    # Link to the skills page
    link_label = ctk.CTkLabel(
        None,
        text="去网页版获取凭证 -> weread.qq.com/r/weread-skills",
        text_color="#4A9EFF",
        cursor="hand2",
        font=("Microsoft YaHei UI", 11, "underline"),
    )
    link_label.pack(anchor="w", padx=20)
    link_label.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://weread.qq.com/r/weread-skills"))

    # ... rest of implementation
    pass