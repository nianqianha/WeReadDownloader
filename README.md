# WeRead 桌面端整书导出器（原型）

**本轮改造交付物：WeRead下载器冻结版网页模式改造**

桌面端复用已安装 `weread.koplugin` 的 LuaJIT WeRead 协议、分片解码、划线 range 校准和注入逻辑；Python 负责网络、章节断点、图片资源、EPUB ZIP 输出和导入包。

## 凭证

凭证只读自 `%LOCALAPPDATA%\WeReadDesktop\weread-live-settings.lua`。该文件从 Kindle 当前设置快照提取，ACL 限制为当前 Windows 用户。导出包和日志均不包含 API key 或 Cookie。浏览器 session Cookie 限定在 `.weread.qq.com` 域，不发给 `i.weread.com` gateway。

### 登录（扫码 / 手动输入）

GUI 顶栏「登录 / 切换账号」打开登录窗口，两条路径二选一。**登录成功后该按钮会变成账号名**（过长截断为 8 字加省略号；手动输入且无账号名时显示「已登录」），下次启动自动恢复成已保存的账号名；凭证缺失时复位为「登录 / 切换账号」。按钮始终是回到登录窗口的入口，点击即可切换账号。

- **扫码登录（默认）**：点开后自动请求 `getLoginUid` 拿到 uid，用 `qrcode`+PIL 渲染确认二维码；后台线程按 5s/8s 长轮询 `getLoginInfo`，扫成功后取 `webLoginVid`/`accessToken` 组装 cookie，并调 `/api/skills/apikeyGet?only_show=1` 取官方 API Key。轮询覆盖插件的完整状态机：`NEED_OTP`/`OTP_NOT_MATCH` 弹出验证码输入（该行默认隐藏，仅在服务端要求时出现）、`OTP_EXPIRED`/`LOGIN_TIMEOUT` 提示重新获取、其它 `logicCode` 直接报错。关窗即通过 `threading.Event` 取消轮询，迟到的队列事件被丢弃。
- **手动输入凭证**：填 `API Key`（`wrk-` 开头，在 <https://weread.qq.com/r/weread-skills> 登录后复制）与 `wr_skey`。两项都必填——`load_credentials()` 强制要求二者同时存在，缺 `wr_skey` 只能搜书和看详情、无法下载章节正文。半填会被拒绝且**不写盘**。

凭证落盘为 `%LOCALAPPDATA%\WeReadDesktop\session-cookies.json`（明文 + `chmod 0o600`；**DPAPI/Windows ACL 加固尚未实现**），下次启动由 `weread_desktop.py::load_credentials()` 合并进 `weread-live-settings.lua`。账号未开通「微信读书技能」时取不到 API Key，会提示先在手机微信读书「我 → 设置 → 微信读书技能」开通。协议移植自本机插件 `weread.koplugin/weread/lib/qr_login.lua`，端点、超时与凭证拼装与该文件一致。

## Windows 桌面 EXE

当前发布为**双程序 onedir 布局**：[`release-windowed/WeReadDesktop/`](release-windowed/WeReadDesktop/)（窗口化 GUI，PyInstaller `console=False`，启动约 0.55 秒、无控制台窗口）+ [`release-windowed/WeReadWorker/`](release-windowed/WeReadWorker/)（隐藏控制台的 UTF-8 worker，承载 `weread_desktop.py` 与 `kindle_data/` 插件资源）。GUI 按 `exe 同级目录/../WeReadWorker/WeReadWorker.exe`、`exe 目录/WeReadWorker/WeReadWorker.exe`、环境变量 `WEREAD_WORKER_EXE` 的顺序查找 worker；分发时两个目录必须保持同级。构建命令见文末。

界面使用 CustomTkinter，包含首次使用说明、登录检查、中文书籍搜索卡片（封面、分类、评分即时显示，分类由 `/store/search` 结果经 `/book/info` 逐本补齐为 `SEARCH_META` 行）、点击后加载封面/简介/分类/评分、第一章检查、单并发默认下载/续传、暂停及中文错误说明。书籍详情来自 WeRead `/book/info`；封面通过 JPEG 流显示。技术日志存于 `%LOCALAPPDATA%\\WeReadDesktop\\logs\\`；原始错误细节只进日志，面板显示中文行动提示。worker 输出强制 UTF-8。登录凭证不会打包在 EXE 中；程序读取本机 `%LOCALAPPDATA%\\WeReadDesktop\\weread-live-settings.lua`。输出/断点位于 `%LOCALAPPDATA%\\WeReadDesktop\\exports\\`。

旧版单文件 onefile EXE 仍在 [`release-final/WeReadDesktop.exe`](release-final/WeReadDesktop.exe)（67,034,806 B，SHA `8be769be`），未被覆盖，仅作历史版本；新版 GUI 3,136,325 B（`5271676C`）、worker 13,759,848 B（`55185379`）。GUI 目录 36.0 MB / 1075 文件（此前 82.1 MB / 1116 文件：numpy、cryptography、psutil 等从未被导入的死重已从 spec 排除，单 exe 体积相对 onefile 下降约 95%）。

已验证打包 EXE（`release-windowed` 16/16 通过）：冷启动出窗 0.55 秒且无 ConsoleWindowClass 窗口；真实搜索中文可解码并渲染 2 张可点击卡片（《超神机械师》`19480992` 分类 `男生小说-游戏竞技`、《道诡异仙》`41794030` 分类 `男生小说-东方玄幻`）；点击卡片 → `--book-info` 填充详情（标题/简介/分类/评分 8.88/10 · 23718 人评分/书籍 ID），poll 循环在流程后仍存活；worker 运行期间同样无控制台窗口。`--check-auth` 返回书架 7 项。页面逻辑测试覆盖 8.88/10 评分显示、空字段、错误翻译、搜索行真实 TAB 分隔与 `SEARCH_META` 分类行。UID 型断点扫描、整章 MD5 retry、空正文/不安全 marker 保护测试通过。当前《超神机械师》1482/1483 个 UID 有效断点，唯一 UID992 目录 wordCount=41 但服务器正文为空；程序保留断点，不生成不完整 EPUB/bundle。`release-windowed` 全量文本扫描 205 个文件，未发现凭证文件、session cookie 或 API key。

- **GUI busy-refusal 修复（2026-09-28）**：解决"点击卡片/搜索/检查登录/导出时 worker 忙 -> UI 卡在 loading 态"的问题。新增 `_is_busy()` 统一判忙、`pending_book` 排队机制、`info_loaded` 标记、`_restore_detail_placeholders()` 恢复占位、stale `BOOK_INFO` bookId 守卫；`search()`/`check_auth()`/`export()`/`probe()` 入口统一前置 busy 守卫；`_handle_done()` 改为 try/finally 并在 finally 中 `_flush_pending_book()`；FAILED 路径不再早退、改为 `_finish_failed_action()` 恢复标签。新增 7 个红测试覆盖：忙时点卡片排队、忙时搜索保留结果、忙时检查登录徽标不变、FAILED book-info 恢复占位、空/失败搜索复位 result_count、忙时导出拒绝 dialog、过期 BOOK_INFO 忽略。单元测试 16/16 通过、打包 GUI 16/16 通过、CLI UTF-8/Lua/密钥扫描全绿。
- **GUI 真实脏数据与封面崩溃修复（2026-09-28）**：修复“点第二本详情加载不出来、出版时间显示 1970-01-01、评分显示 0.0/10、状态栏只显示『操作没有完成』”。
  - `TclError: image "pyimageN" doesn't exist`：`CTkLabel._update_image()` 在 `image=None` 时是空操作，内层 Tk label 仍指向旧图名；旧 `CTkImage` 被 GC 后 Tk 删除该图，此后任何一次 text 更新都会抛异常，异常在 `_start_book_info` 中途抛出导致 **worker 根本没启动**。改为 `self._image_refs` 持有全部 CTkImage 直到进程结束。
  - WeRead 用 Unix 纪元哨兵代替缺失字段：`publishTime='1970-01-01'`、`newRating=0`。`_is_missing_detail()` 过滤纪元日期，`rating_text`/`card_rating_text` 把 `<=0` 视为无评分。
  - `ERRORS_ZH` 新增 `No readable chapters in WeRead catalog` 条目，状态栏不再回落 FALLBACK_ZH。
  - 单元测试重定向 `LOG_DIR`/`OUTPUT` 到临时目录，不再污染真实 `%LOCALAPPDATA%` 日志。
  - 新增 5 个红测试（纪元出版时间、零评分、缺可读章节错误翻译、封面生命周期、失败路径不复现纪元时间）与 1 个真实数据端到端脚本；`test_desktop_app.py` 22/22 通过。

- **GUI 扫码登录 + 手动输入凭证（2026-09-28）**：新增 `weread_login.py`，把本机插件 `weread.koplugin/weread/lib/qr_login.lua` 的登录协议移植到 Python（`getLoginUid` → 渲染二维码 → 5s/8s 长轮询 `getLoginInfo` → `userInfo` + `apikeyGet` 组装凭证），顶栏新增「登录 / 切换账号」入口，登录窗口提供**扫码**与**手动输入**两种模式。
  - 协议层：`begin_login()`/`poll_login()`/`complete_login()`/`interpret_poll()`/`save_credentials()`/`load_saved_credentials()`/`render_qr_png()`。`interpret_poll()` 复刻插件状态机（`NEED_OTP`/`OTP_NOT_MATCH`/`OTP_EXPIRED`/`LOGIN_TIMEOUT`/其它 `logicCode`），并把「无法识别的响应」判为失败而非无限轮询。
  - GUI 层：轮询跑在后台线程，所有结果经既有 `events` 队列回主线程；关窗用 `threading.Event` 取消，迟到事件被 `_handle_login_event()` 丢弃。验证码输入行**默认隐藏**，仅在服务端要求时出现。`CTkImage` 同样进 `_image_refs`，避免复现既有的 `pyimageN` GC 崩溃。
  - 顺带修一个真实缺陷：登录检查失败原先只改徽章不给出路（FAILED 行已先写过状态栏，`_handle_done()` 的 `last_failure` 分支会跳过 auth 处理）。抽出 `_report_login_problem()`，让「非零退出」与「FAILED」两条路径都给出「扫码 or 手动输入」的下一步；有凭证但失效时只提示重扫，不弹登录窗。
  - 另修一个**既有**缺陷（与登录无关，搜索时暴露）：`_drain_events()` 无上限排空队列，`_log()` 每行都在主线程做一次文件追加 + 4 次 Tk 文本框操作，worker 突发几百行时主线程阻塞过久 → 窗口被判「未响应」。改为每 tick 最多 200 条、日志面板每 tick 批量插入一次、面板行数上限 4000。实测 3000 行突发摊到 15 个 tick，最差单 tick 344ms。
  - **移除「首次使用」按钮与说明对话框**（用户要求）：该对话框不仅冗余，且在引入登录窗口后**内容已失真**——第 1 条称「本程序不保存或打包账号密码，只读取登录设置」，而登录窗口现在会把凭证写入 `session-cookies.json`；第 2 条仍指引用户自行准备 `weread-live-settings.lua`，正是本次删除掉的旧流程。第 3/4/5 条（搜索、续传、断点保护）在主界面已有内联提示（「中断后可点继续下载」「技术日志会显示下载进度」）及「打开下载目录」「查看技术日志」按钮。顶栏现只剩账号名按钮、「检查登录」与状态徽章三处。同步修掉 `weread_web.py` 两处指向「首次使用」的悬空文案（改为指向图形版扫码登录、以及直接给出 `OUTPUT` 路径）。
  - 测试：`test_weread_login.py` 16/16（协议编排、状态机、凭证落盘、二维码**墨水量 + cv2 解码回环**——原测试只验尺寸，空白二维码会漏过）；`test_desktop_app.py` 31/31（含登录窗 8 个新用例）；`test_weread_desktop.py` 12/12。
  - 真实网络验证：`getLoginUid` 返回 36 位 uid，长轮询正确进入 `LoginPending` 等待态，渲染出的二维码用 cv2 `detectAndDecode` 成功解回原 confirm URL。冻结包 1.0s 起窗，登录入口在位（窗口未真正获得前台焦点时点击会落空——Windows 前台焦点抢夺限制，非功能缺陷）。
  - 打包：`release-qrlogin/`（未覆盖旧版），GUI 38.3 MB / exe 3,356,857 B，worker 复用已验证的 133 MB bundle；`qrcode` 加入 `collect_all`。凭证扫描 464 文件 0 命中。
  - UI 取舍：登录窗固定 620x620（125% DPI 下实测 775x775），手动页卡片改为贴合内容、扫码页与手动页共用同一尺寸，避免切页时窗口变形。
  - **未完成**：`session-cookies.json` 仍是明文 + `chmod`，DPAPI / Windows ACL 加固未做。

- **启动耗时实测（2026-09-28，更正归因）**：源码模式首屏 0.51–0.62s；冻结 onedir 首屏 5.15–6.43s。逐段打点显示耗时发生在**用户代码第一行执行之前**（`script-start` 已耗 5.7s），而 customtkinter 导入仅 0.23s、构建全部控件仅 0.01s。逐项对照实验：**纯标准库冻结包 0.108s，PIL-only 冻结包 5.4–6.3s**——触发条件是包内是否含原生扩展，而非包体积、签名、UPX、onedir 引导（`collect_all` / `windowed` / `console` 切换均已证伪，Defender 实时防护服务未运行）。系统运行卡巴斯基（`klids.K4W-21-25` 过滤驱动已加载）会深扫新构建的 EXE；**"未签名"并非成因**（此前结论有误，此处更正），确切扫描对象未经逐文件确认。缓解方式：卡巴斯基「排除项 → 受信任文件夹」加入本程序目录（需用户自行操作）。

## WeRead下载器冻结版网页模式改造（当前新发布形态）

GUI 冻结包启动要 5.15–6.43s，根因是卡巴斯基对新构建、未签名 EXE 做深度扫描，且已证实与包体积、Defender、UPX、onedir 引导都无关；只要冻结包内含 PIL/tkinter 原生扩展就会被深扫，纯标准库不受影响。**网页模式据此把「画界面」整体移出冻结包**：exe 只跑一个本地 HTTP 服务，界面由浏览器负责。

发布为**双程序 onedir 布局**：[`release-web/WeReadDesktop/`](release-web/WeReadDesktop/)（`console=False`，**纯标准库**）+ [`release-web/WeReadWorker/`](release-web/WeReadWorker/)（隐藏控制台 worker，承载 LuaJIT/lxml 下载引擎）。两目录必须同级；也可用 `WEREAD_WORKER_EXE` 覆盖 worker 路径。

- **`weread_web.py`**——只用标准库（`http.server`/`json`/`secrets`/`subprocess`/`webbrowser`），**不 import 任何 GUI 或图像库**。`WebController` 是 `desktop_app.py` 逻辑层的无头移植：判忙拒忙、`pending_book` 排队、`info_loaded` 标记、`_handle_done()` 的 try/finally + `_flush_pending_book()`、FAILED 路径恢复占位、stale `BOOK_INFO` 的 bookId 守卫，逐条保留；并覆盖 worker 全部五个 action（search / book-info / auth / probe / export）。
- **`weread_core.py`**——GUI 与网页模式**唯一**的中文措辞来源（`rating_text`/`card_rating_text`/`book_detail_lines`/`_is_missing_detail`/`friendly_error`/`redact_secrets`/`parse_*`/`worker_command`），保证两个界面对同一事件说同一句话。`desktop_app.py` 改为引用它。
- **封面不进服务端**：页面用 `<img>` 直接从微信读书 CDN 取图，加载失败降级为「暂无封面」占位。因此冻结包不需要 PIL，GUI 侧 `_image_refs` 防 GC 那套也不需要了。
- **安全**：仅绑 `127.0.0.1` + 随机端口；每次启动生成一次性随机 token，所有请求都必须带（header / query / cookie 三选一）；`Host` 头必须等于本次随机 `host:port`（防 DNS rebinding），非安全方法另校验 `Origin`/`Sec-Fetch-Site` 且从不返回 CORS 头（防 CSRF）；token 经 303 换成 `HttpOnly; SameSite=Strict` cookie 后即从地址栏移除，页面再配 `Referrer-Policy: no-referrer`，避免 token 经封面请求的 Referer 外泄。页面为单文件内联（`webui.html`），无框架、无外部资源，响应头带按响应随机 nonce 的严格 CSP。
- **凭证不变**：不保存也不打包账号密码，仍只读 `%LOCALAPPDATA%\WeReadDesktop\weread-live-settings.lua`；导出包与日志均不含 API key 或 Cookie。`logs\web-session-<端口>.txt` 只记端口、PID 与启动 URL（token 仅对本机本用户本进程有效，非微信读书凭证）；按端口命名，多个实例不会互相覆盖。
- **登录（两个通道，照搬图形版）**：顶栏「登录 / 切换账号」按钮，有账号时直接显示账号名。扫码登录与手动输入凭证两个 tab，副标题「扫码后凭证只保存在本机，不进入日志或安装包。」，验证码输入框只在微信读书真的要验证码时才出现。登录检查失败会按图形版的分流给出唯一下一步：有凭证 → 提示重新扫码；无凭证 → 提示扫码或手动输入。**本地服务没在运行时，登录对话框里会直接说明原因**（而不只写在被对话框遮住的底部状态栏），并清掉已失效的二维码。
  - **登录协议跑在 worker 里**（`weread_worker.py --login`），网页端只做监督。原因是 `weread_login.py` 依赖 `requests` + `qrcode` + `PIL`，直接搬进网页包会把原生扩展请回来，0.6s 首屏当场作废。worker 在加载引擎前分流，登录不必付 LuaJIT 初始化代价。
  - **二维码不进网页包的解码路径**：worker 出 PNG，网页端只按字节转发给浏览器渲染。手动输入通道更是纯标准库（读写 `session-cookies.json`），完全不联网。
- **退出登录**：在登录对话框底部（仅在存在扫码会话时出现）。**它只删 `session-cookies.json`**，并明确告知 `weread-live-settings.lua` 仍持有一份会话、彻底注销需自行删除该文件——因为引擎把 .lua 当强制基础、json 只是覆盖层，删 json 并不会真的退出登录。
- **顶栏账号名的含义**：不是"本次登录后才有"，而是**每次启动从 `session-cookies.json` 的 `account.name` 读出**，所以重开仍在。手动输入凭证会把名字置空（与图形版一致），此时按钮显示「登录 / 切换账号」但凭证其实已生效——判断是否可下载请看「检查登录」的结果。
- **网页图标**：`/favicon.ico` 返回随包分发的多尺寸图标，标签页不再显示空白文档图标。
- **实测**：冻结 exe 服务就绪 **0.714–0.916s**（n=4，mean 0.798），安静机器上达标 < 1.5s；**进程内成本仅 0.070s**。同一 exe 在另一执行方并发构建/导出时曾波动到 14.259s——波动来自进程启动/冻结包 I/O/AV 深扫，与代码无关，验收须在机器空闲时做。`_internal` 56 文件 / 13.07MB，**无 PIL / qrcode / requests / tkinter / _tkinter / tcl86t.dll / tk86t.dll**。
- **功能**：登录/扫码、搜索、详情、**先检查第一章**、**开始下载 / 继续下载**、进度条、暂停、打开下载目录、技术日志面板。下载控件固定在底部独立一行，不必滚动详情就能点到。真实实测：probe 首章 1059 章目录 / 34 条划线 / 34 个注入 marker；下载推进到 32/1059（3%）；**UID 断点续传 `Reusing 25 complete chapter(s) by UID; 1034 remain`**；暂停后断点保留，可继续；落盘 77 个章节文件而**未发布任何 `.epub` / `.weread.zip` / `bundle.json` / `manifest.json`**，不完整产物保护在真实数据上守住。并发数上限仍为 4、默认 1（真实 `jobs=3` 曾触发 LuaJIT `SystemError`，未放宽）。
- **日志**：面板走 `GET /api/log?since=N` 增量拉取（控制器内存 600 行环形缓冲），不塞进每次 800ms 的状态轮询；面板内容在进内存前就完成凭证脱敏。

## 命令行

从 Kindle 桌面根目录运行：

```powershell
python "05_补丁与其他\桌面端WeRead下载器\weread_desktop.py" --check-auth
python "05_补丁与其他\桌面端WeRead下载器\weread_desktop.py" --search "书名关键词"
python "05_补丁与其他\桌面端WeRead下载器\weread_desktop.py" 书籍ID
```

全书导出默认写入 `%LOCALAPPDATA%\WeReadDesktop\exports\<书名>-<书籍ID>\`，包含 EPUB、`manifest.json`、流式 `reviews.jsonl`、`bundle.json` 和 `.weread.zip`。每章原文、CSS、review 与图片断点放在同级 `.work` 目录；中途失败后重新执行相同书籍 ID 会复用校验匹配的章节文件。桌面文件夹仅当前 Windows 用户可访问。完成后可把 `.weread.zip` 复制到 Kindle 的可读存储目录。

## Kindle 导入

KOReader → WeRead → **Import desktop WeRead bundle**，选择 `.weread.zip`。导入器会检查文件成员/路径、版本、章节映射、reviews 行与 EPUB 头，再把 EPUB 安装到用户下载根目录、按章节将 reviews 导入 `thoughts.db`、登记 `embedded=true` 的注释文档元数据，并写入书架索引。导入不访问 WeRead 网络 API。大 bundle 在 Kindle 上解压写入临时目录，EPUB 用 64 KiB 流式复制；review 按章节事务写入数据库。

## 当前验证状态 / 限制

- 已通过离线单章假 API 端到端：Lua 协议模块加载、标准 EPUB ZIP、章节正文、bundle manifest/reviews 对齐、导入清单路径安全。
- 既有真实 ch.186 注释坐标 fixture 仍通过；桌面注入直接调用相同 Lua `Annotations.process()`，不另实现范围算法。
- WeRead `--check-auth` 实测读取书架数量为 8。真实 ch1 probe 获取正文 8449 bytes、34 个 underline ranges、313 个 page-review rows，修复 Lua bridge 后 ch1 产生 34 markers。真实整书 jobs=3 落盘 30 章后 LuaJIT `SystemError`，jobs=1 由 checkpoint 续传 106 章；chapterIdx111/UID115 catalog 显示 `price=0.1, paid=0`，e shards 解码仅 448 bytes plain text、无 XHTML body，当前归一化后只有 616 bytes；21 ranges 仅 2 个可安全映射，paid/短正文 guard 停止。Checkpoint 保留在 `exports/道诡异仙-41794030.work`，没有发布不完整 EPUB/bundle。
- 已发现此书 catalog `chapterUid` 为 number；现已保留 API 原始 UID 类型，reviews 能返回 thought pages。UID115 被标记未购买且服务端返回短占位正文；导出要求完整章节正文且 marker coverage 全部通过，不会生成缺正文/缺注释成品。拿到该章合法正文后再重新续传检查。
- 图片 TAR/ZIP 和远程图片流式落盘并有大小上限；每章正文/reviews/CSS 断点原子保存；所有章节成功后才原子生成 EPUB。HTTP 有有界退避，`--jobs 1..4` 可调但默认 1：真实 jobs=3 出现 LuaJIT `SystemError`，并发稳定性待修；fake API barrier 仅证明本地调度可重叠请求，真实吞吐尚未测。
- 导入 ZIP 校验成员路径、章节映射、EPUB 结构和大小；reviews 逐章通过单个 SQLite 事务替换对应章节数据；Settings 索引保留旧快照，导入失败恢复旧 EPUB/settings。导入器尚未实机部署验收。
- **完整真实整书下载尚未完成**。UID115 catalog 标记付费/未购买且返回占位正文；不能把现有 106 章 checkpoint 当成可导入整书。需先在 WeRead 账号获得该章可读正文，再续传并验证完整 EPUB/bundle；随后部署 importer，实测 Kindle popup/回滚。

## 离线测试

```powershell
python "05_补丁与其他\桌面端WeRead下载器\test_weread_desktop.py"
python "05_补丁与其他\桌面端WeRead下载器\test_desktop_bundle.py"
python "05_补丁与其他\桌面端WeRead下载器\test_desktop_app.py"
python "05_补丁与其他\桌面端WeRead下载器\test_weread_web.py"
python "05_补丁与其他\桌面端WeRead下载器\test_weread_login.py"
python "05_补丁与其他\桌面端WeRead下载器\test_weread_core.py"
```

`test_weread_login.py` 覆盖扫码协议与凭证落盘，其中二维码用例会断言**实际墨水量**并在装有 cv2 时做解码回环；只验尺寸的写法会让空白二维码蒙混过关。`test_weread_core.py` 覆盖共享逻辑，其中单实例守卫验证「首个调用者拿到名额、第二个被拒、不同程序互不锁死、句柄不泄漏、API 异常时放行」。

## 单实例

重复双击快捷方式曾会留下多个窗口、每个各带一个 worker，网页版还会多开随机端口的本地服务并共同写同一个会话文件。现在两个程序各自用命名互斥体守住单实例（`weread_core.claim_single_instance()`）：

- **图形版**：第二次启动不再开新窗口，而是把已在运行的窗口叫到前台（按 `APP_TITLE` 查找），然后退出。
- **网页版**：第二次启动弹提示告知「网页版已经在运行」，然后退出。它**不会**去读另一个实例的 `web-session-*.txt` 复用其 token。
- 两个版本用不同的互斥体名，**可以同时各开一个**。
- 互斥体句柄全程持有（`_SINGLE_INSTANCE_HANDLES`），否则句柄被回收即等于关闭互斥体，第三个副本就能进来。若互斥体创建失败则**放行**——宁可多开，也不要因为 API 异常而拒绝启动。
- 安装器侧用 `AppMutex=WeReadDesktop-Graphics,WeReadDesktop-Web` 声明同样的名字，安装/卸载时能识别正在运行的实例。

Lua 侧 bundle 回归（需 lupa/LuaJIT；先从字节加载 `desktop_bundle.lua` 绕过 LuaJIT 对非 ASCII 路径的限制，通过时输出 `PASS test_desktop_bundle.lua`）：

```powershell
python "05_补丁与其他\桌面端WeRead下载器\test_desktop_bundle_lua.py"
```

## 产物位置（硬性约束）

**所有打包产物与中间物只允许放在本项目目录内，桌面根目录一律不放。**

- 产物：`05_补丁与其他\桌面端WeRead下载器\release-<名称>\`
- 中间：`05_补丁与其他\桌面端WeRead下载器\build-<名称>\`

GUI 与 worker 必须是**同级目录**——`worker_command` 只认 `exe 同级`、`../WeReadWorker/`、`./WeReadWorker/` 三种位置，因此布局固定为：

```text
release-<名称>\
├── WeReadDesktop\    # 窗口化 GUI
└── WeReadWorker\     # 隐藏控制台 worker
```

**禁止**把 `WeReadDesktop\`、`WeReadWorker\` 复制到 `C:\Users\asus\Desktop` 或任何桌面根目录位置。构建完成后如需验证，直接运行 `release-<名称>\WeReadDesktop\WeReadDesktop.exe`，不要靠桌面副本。桌面根目录只允许存在：`WeRead 网页版（0.6s 启动）.lnk`、`WeRead 图形版（二维码登录）.lnk`、`WeReadDesktop-源码调试.bat`，以及 `各类\Kindle\...` 下的项目目录。

若使用图形版做人工验证，卡巴斯基排除项应指向 `release-<名称>\WeReadDesktop`；或直接用 `WeReadDesktop-源码调试.bat` 走源码模式（`pythonw.exe`，进程受信任，约 0.5 秒，无需任何排除项）。

**测试其它实例时的边界**：不得读取 `%LOCALAPPDATA%\WeReadDesktop\logs\web-session-*.txt` 里的 token 去调用**别人已启动的实例**的 API。要测就自己启动一个实例、用自己打印的端口和 token 操作自己的进程。

**桌面例外（用户明确要求）**：桌面上除上述四项外，还允许存在**单个安装包** `WeReadDesktop-安装程序.exe`（即 `release-installer\WeReadDesktop-Setup.exe` 的副本），供用户双击安装。**只允许这一个 .exe**，仍然禁止把 `WeReadDesktop\`、`WeReadWorker\` 等解压后的 bundle 目录或任何构建中间物放到桌面。

## 安装器（Setup.exe）

`installer\WeReadDesktop.iss` 是 Inno Setup 6 脚本，编译产物为 `release-installer\WeReadDesktop-Setup.exe`（约 48.5 MB，压缩前 185.6 MB）。用户双击后得到标准安装向导：**选择安装位置 → 勾选要装的版本 → 勾选桌面快捷方式 → 安装完成页询问是否立即启动**，并带卸载程序。

编译（Inno Setup 装在用户目录，用 `ISCC.exe` 直接调）：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\WeReadDesktop.iss
```

`installer\ChineseSimplified.isl` 是官方简体中文语言包（Inno Setup 自带的 29 个语言里没有中文），**`.iss` 与 `.isl` 都必须存为带 BOM 的 UTF-8**，否则中文会按系统 ANSI 代码页读成乱码。

**安装后的目录结构**：

```text
{app}\                     默认 {autopf}\WeReadDesktop，即 Program Files\WeReadDesktop
├── WeReadDesktop\         图形版（二维码登录）
├── WeReadWeb\             网页版（0.6s 启动）
└── WeReadWorker\          下载组件，两版共用这一份
```

两个 exe 分别住在 `WeReadDesktop\` 与 `WeReadWeb\`，worker 放在它们**共同的父级**——`worker_command()` 的查找顺序第二条正是「exe 同级目录/`../WeReadWorker/`」，因此完整安装后 worker 只有一份（191 MB），而不是两份 266 MB。

`PrivilegesRequired=admin` 写 Program Files 需要提权；同时设了 `PrivilegesRequiredOverridesAllowed=commandline`，可用 `/CURRENTUSER` 降级装到 `%LOCALAPPDATA%\Programs`，非管理员环境也能安装。

**踩过的坑**：`[Types]` 的类型名若与 `[Components]` 的组件同名（原先都叫 `graphics`/`web`），Inno Setup 解析组件选择时会歧义。已把类型改名 `onlygraphics`/`onlyweb` 避开。另外命令行 `/COMPONENTS=` 在默认类型为 `custom` 时不会按预期过滤，要验证单个版本应改用 `/TYPE=onlygraphics`。

## 构建

**当前发布形态**（含扫码登录，输出到新目录 `release-qrlogin/`，不覆盖旧版）：

```powershell
python -m PyInstaller --clean --noconfirm --distpath release-qrlogin --workpath build-qrlogin WeReadDesktop.spec
# worker 本次无改动，直接复用已验证的 bundle：
Copy-Item release-windowed\WeReadWorker release-qrlogin\WeReadWorker -Recurse -Force
```

`WeReadDesktop.spec` 打包 customtkinter/darkdetect/**qrcode**，排除 lupa/lxml/插件源码。`qrcode` 必须进 `collect_all`——其 image factory 由新版 qrcode 动态加载，静态分析可能漏掉。GUI 只改前端时无需重建 worker。

从项目目录执行（历史版本，输出到 `release-windowed/`，不覆盖旧版 onefile）：

```powershell
python -m PyInstaller --noconfirm --distpath release-windowed --workpath build-windowed WeReadDesktop.spec
python -m PyInstaller --noconfirm --distpath release-windowed --workpath build-windowed WeReadWorker.spec
```

`WeReadDesktop.spec` 为 `console=False` 的 onedir GUI（打包 customtkinter/darkdetect，排除 lupa/lxml/插件源码）；`WeReadWorker.spec` 为 `console=True` 的 onedir 控制台程序（打包 `weread_desktop.py`、插件 `kindle_data/` 与 lupa，供 GUI 隐藏调用）。两者的 `_internal/` 布局由 PyInstaller 6 自动处理。

网页模式（输出到新目录 `release-web/`，同样不覆盖旧版）：

```powershell
python -m PyInstaller --noconfirm --distpath release-web --workpath build-web WeReadWeb.spec
python -m PyInstaller --noconfirm --distpath release-web --workpath build-web WeReadWorker.spec
```

`WeReadWeb.spec` 为 `console=False` 的 onedir 网页模式，除 `webui.html` 外不打包任何第三方包，并显式排除 `tkinter`/`_tkinter`/`PIL`/`customtkinter` 与下载引擎依赖——**这是首屏能从 5–6s 降到 0.7s 的硬约束，往包里加任何原生扩展都会把它破坏**（已有子进程测试守护：`test_weread_web.py::test_source_stays_free_of_gui_and_engine_libraries`）。
