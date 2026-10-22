# WeReadDownloader 技术文档与变更日志

> 样式沿用《Kindle-技术文档与变更日志.md》（Keep a Changelog 分类 + Diátaxis 四分法）。
> 本文件是 WeReadDownloader 项目的**唯一权威运维文档**；Kindle 主文档 §四 保留每次
> 工作的索引条目，细节以本文件为准。

---

## 目录

- [〇、接手速览](#〇接手速览新会话新-agent-先读这里)
- [一、环境与路径速查](#一环境与路径速查)
- [二、架构速记](#二架构速记)
- [三、功能演进时间线](#三功能演进时间线)
- [四、变更日志](#四变更日志keep-a-changelog-格式)
- [五、已知问题与限制](#五已知问题与限制)
- [六、运维与排障手册](#六运维与排障手册)
- [七、附录](#七附录)

---

## 〇、接手速览（新会话/新 Agent 先读这里）

### 0.1 当前状态看板（每轮改完必须更新）

| 项 | 状态 | 备注 |
|---|---|---|
| 安装版本 | 1.0.4（安装包 SHA8 `DA420F0E`，53,021,646 B，在桌面） | **待用户实测**；含 §四 2026-10-16 (2) 的 9 个 PR + (3) 的 W10 修复；**无组件选择界面、不含网页版**（按用户要求） |
| 凭证状态 | ✅ 可用（api_key + 扫码会话） | §6.3 凭证位置；wr_skey 会过期，重新扫码即可 |
| 上游 master | ✅ 16 个 PR 已合入（协作者 15 个 + 测试/CI 1 个），issue #1–#8 + #22/#24/#25/#28/#30/#32 已关闭 | §四 2026-10-16 (2) 与 2026-10-18 (0)；**CI tests 作业已全绿**
| 运行入口（推荐） | ✅ `WeReadWorkerPatched\WeReadWorker.exe`（工作区副本，含死链图片缓存补丁） | §四 2026-10-15 (2)；仓库 master 已修 GUI 下载链，但 W1 提速补丁仍未入仓 |
| GUI 可用性 | ✅ 下载链已修（master）：标注模式、探针参数、凭证就绪、书架失败态、**排队下载**（勾选多本自动续跑） | 1.0.4 安装版不含排队下载，需重新打包分发 |
| 本地补丁 | ✅ `_internal\weread_desktop.py` `9BEC87F6`（原版 `C4275775`） | 副本位于 `C:\Users\asus\dsh-workspace\WeReadWorkerPatched\`；搬回仓库仍待定（问题 W1） |
| 下载进度 | 《战国策校注》✅ 23/23 已打包；《后汉书》136/155、《汉书》147/171 已暂停 | 断点在 `%LOCALAPPDATA%\WeReadDesktop\exports\`，续传不重复下载 |
| 提速补丁效果 | 约 10 倍（1.5 分/章 → 约 5~6 章/分，双书并行 jobs=2） | §四 2026-10-15 (2) 实测数据 |
| 自更新 | ✅ 应用内「检查更新」可用（查 GitHub Releases，拉起官方 Setup.exe 覆盖） | §四 2026-10-18 (0)；版本号单一事实源 `weread/version.py` |

### 0.2 五条铁律（违反就会踩已知坑）

1. **改完代码必须更新本文件**——§四加条目、§0.1改看板、§7.1记 sha、新故障进 §6.5。
2. **未经用户明确批准，禁止 git push、禁止发布 Release/安装包**——本仓库关联公开
   GitHub（origin: nianqianha/WeReadDownloader）。补丁、修复、文档一律先落在本地，
   是否推送/发布由用户逐次决定；文档与日志里不得写"已推送/已发布"，除非用户当轮
   明确要求过。`pre-push` 钩子只扫凭证，拦不住内容变更，这条只能靠自觉遵守本铁律。
3. **强杀 worker 后必须清 `export.lock`**——`atexit` 清理不会执行，残留锁会让下次
   启动直接 `SystemError` 失败（§6.5 排障第一行）。
4. **凭证文件是两份契约**——`weread-live-settings.lua`（api_key 基础文件，缺失即
   "Credential file not found"）+ `session-cookies.json`（扫码 overlay，DPAPI 密封）。
   任何只写其一的登录方式都是坏的（§四 2026-10-14 上游条目）。
5. **速率限制是按账号的**——双书并行 + jobs=2 的聚合吞吐优于串行；被 426 限流时
   程序自动降并发退避，被拒章节保留断点，冷却 60s 后重跑即可（§6.5）。

### 0.3 文件布局速查（"我要改的东西在哪"）

```text
本机
  C:\Users\asus\Desktop\各类\weread-downloader\          ← 仓库（源码工作副本）
      weread\desktop_app.py                               ← GUI 主程序（含 export 回调 bug 所在）
      weread\weread_desktop.py                            ← 下载引擎（与已安装版同源）
      weread\weread_login.py                              ← 登录/凭证（DPAPI 密封写入方）
      weread\first_run.py                                 ← 首运行向导/凭证文件生成
      tests\                                              ← 40 个测试文件
  C:\Program Files\WeReadDownloader\                      ← 已安装程序（管理员权限）
      WeReadDesktop\WeReadDesktop.exe                     ← GUI
      WeReadWorker\WeReadWorker.exe + _internal\          ← 下载引擎（明文 .py 可改）
  C:\Users\asus\dsh-workspace\WeReadWorkerPatched\        ← ★ 打了补丁的 worker 副本（推荐用这个跑下载）
      _internal\weread_desktop.py                         ← sha8 9BEC87F6（含死链图片缓存补丁）

用户数据（与程序位置无关，重装不丢）
  %LOCALAPPDATA%\WeReadDesktop\
      weread-live-settings.lua                            ← api_key 基础凭证（明文 Lua 表）
      session-cookies.json                                ← 扫码会话（DPAPI 加密）
      exports\书名-书籍ID\                                ← 成品（EPUB/weread.zip/manifest）
      exports\书名-书籍ID.work\                           ← 断点工作目录（state.json + 章节缓存）
      logs\desktop-general.log                            ← GUI 总日志
      logs\desktop-<书籍ID>.log                           ← 单书日志
```

### 0.4 一个命令确认下载引擎在哪、哪个能用

```powershell
# 原版（Program Files，含 GUI bug 对应的 CLI bug）
& "C:\Program Files\WeReadDownloader\WeReadWorker\WeReadWorker.exe" --check-auth

# 补丁版（推荐；死链图片只请求一次）
& "C:\Users\asus\dsh-workspace\WeReadWorkerPatched\WeReadWorker.exe" --check-auth
# 输出 AUTH_OK; shelf entries: N 即凭证与引擎均正常
```

---

## 一、环境与路径速查

### 1.1 程序与版本

| 项 | 值 |
|---|---|
| 产品 | WeReadDownloader（微信读书桌面导出器） |
| 上游仓库 | `github.com/nianqianha/WeReadDownloader`（本地仓库 `C:\Users\asus\Desktop\各类\weread-downloader`） |
| 安装版本 | 1.0.2（2026-10-15 安装） |
| 运行时 | PyInstaller onedir，Python 3.9，LuaJIT 2.1（lupa）承载协议脚本 |
| 数据目录 | `%LOCALAPPDATA%\WeReadDesktop\` |
| GUI 进程 | `WeReadDesktop.exe`（customtkinter） |
| 引擎进程 | `WeReadWorker.exe`（CLI，独立于 GUI 可单独运行） |

### 1.2 CLI 用法（引擎直用，绕过 GUI bug）

```text
WeReadWorker.exe <book_id> --no-annotations --jobs 2   # 纯正文模式（无划线无想法）
WeReadWorker.exe --check-auth                          # 验证凭证（api_key 通道）
WeReadWorker.exe --search "关键词"                      # 搜索（输出 bookId 列表）
WeReadWorker.exe --book-info <book_id>                 # 单本书元数据
WeReadWorker.exe --shelf                               # 书架列表 JSON
```

- `--no-annotations`：跳过 `/book/underlines` 与 `/book/readreviews` 两次请求/章，
  最快最小；`--no-thoughts` 为中间档（要划线不要想法）。
- `--jobs` 1–4；1 最稳（LuaJIT 逐章单线程），2 在双书并行时聚合吞吐最好。
- ⚠️ `--probe-book` 存在参数个数 bug（问题 W4），CLI/GUI 均会失败，等待上游修复。

### 1.3 标注三模式与导出物

GUI 的下拉框四档对应下面的 worker 参数（2026-10-21 起「注释和想法」是默认档）：

| 模式（GUI 标签） | worker 参数 | 划线 | 注释 | 想法 | 产物 reviews.jsonl |
|---|---|---|---|---|---|
| `注释和想法`（默认） | `--unified-appendix --appendix-scope both` | 有 | 进书末附录 | 进书末附录 | 有（完整） |
| `注释` | `--no-thoughts --unified-appendix --appendix-scope notes` | 有 | 进书末附录 | 无 | 有（仅落点） |
| `想法` | `--unified-appendix --appendix-scope thoughts` | 有 | 不上附录（注角标保持原样） | 进书末附录 | 有（完整） |
| `纯正文` | `--no-annotations` | 无 | 无 | 无 | 无 |

`--unified-appendix` 与 `--appendix-scope` 是档位附带的开关，不是独立的第 N 种模式；
CLI 上两者都是显式参数（默认不带）。

成品目录：`exports\书名-书籍ID\`：`书名 - full.epub`（唯一的成品）、`manifest.json`
（含 omissions 缺失说明）、`reviews.jsonl`。2026-10-22 起不再生成 `.weread.zip` 导入包。
断点目录：同名 `.work\`（`state.json` 记 `completed/total`，`chapter-uid-*.body/css/json`
逐章缓存，`export.lock` 运行锁）。

### 1.4 统一附录（注释和想法）——功能规格

**目标**：把一本书的「注」与「想法」从章末/划线旁路收敛到**书末一个独立章节**
`注释和想法`，正文只留角标，点角标可跳转；不改变下载链路与断点续传。

**产品规则**（已定）：

| # | 规则 | 依据 |
|---|---|---|
| R1 | 角标紧跟对应位置（划线末/注角标原位），正文原文不动 | 用户 2026-10-16 拍板 |
| R2 | 想法正文全部内嵌、不截断 | 同上 |
| R3 | **注与想法都进书末附录**：注按 ~3000 条/册分册（单册约 1 MB），想法单独一册 | 2026-10-20 实测（单文件 12 MB 会卡） |
| R4 | 旧书不重做；仅对新下载书籍启用 | 用户拍板 |
| R5 | **默认开启**：GUI 下载区的默认档就是「注释和想法」；CLI 上仍是显式 `--unified-appendix` | 用户 2026-10-21 指定「下载选择改成参考书的形态」 |
| R6 | 指向附录的角标必须在附录有条目（硬断言）；「有条目无角标」如实上报不失败 | 研究笔记 §3 校验项 |

**接口**（CLI/引擎）：

- 新开关：`--unified-appendix`（CLI 默认不带）。打开时在书末追加附录章节，manifest.json 记
  `appendix: {files, title, notes, thoughts, thought_badges, chapters, notes_parts,
  notes_in_appendix, orphan_entries, unlinked_underlines}`。
- 章节产物 `chapter-uid-*.body` 仍是唯一正文事实源；附录由 `write_epub()` 打包阶段
  从 `reviews.jsonl` + 章末注 DOM 生成，**不改下载链路、不改断点结构**。因此对已有断点的书
  切换开关只会重新打包，不会重下章节。

**GUI**：`_ANNOTATION_MODES`（`desktop_app.py`）是档位唯一事实源——下拉框选项、默认值、
worker 参数都由它派生，四档分别是 `注释和想法`(both) / `注释`(notes) / `想法`(thoughts) /
`纯正文`(无附录)。`_annotation_mode()` 遇到未知标签回退到默认档（W3 的教训：标签与映射
漂移会让导出直接崩）。`_queue_args()` 是唯一拼 worker 参数的地方，`probe()` 不传附录开关。

**验收场景**：

| 场景 | 期望 |
|---|---|
| 小书（三字经 11 章/34 想法/0 注）开 `--unified-appendix` | 只生成想法册；条目数 == 角标数 |
| 大书（后汉书前 40 章，2026-10-21 实测 10,723 注/679 想法） | 4 册注（约 0.47–1.05 MB/册）+ 1 册想法；正文注角标 == 附录注条目 |
| 后汉书全本（155 章，约 42,577 注） | 14 册注 + 1 册想法；单册约 3000 条 ≤ 1.1 MB |
| GUI 默认档 | 下拉框默认「注释和想法」；首次下载前即可改选（不需要先跑完一次任务） |
| GUI「注释」档 | 只生成注分册，不生成想法册；正文不插 `[N]` 角标 |
| GUI「想法」档 | 只生成想法册，不生成注分册；注角标保持原样（不指向不存在的附录） |
| 开关关闭 | 导出物与当前版本逐字节一致（回归） |
| 正文角标与附录条目数不一致 | 构建失败并报错，不产残缺 EPUB |

**已知边界**（来自 W12/W13/W14，实现时不得违反）：

- 阅读器对**单文件体积**敏感（W12）：注附录实测单文件 12.2 MB 会卡，按 ~3000 条/册
  分册后单册约 1 MB；不做「一想法一文件」的碎片化分册。
- crengine 不实现 `<details>`/`:target`（W14 相关）：不能靠折叠隐藏，角标跳转走 `epub:type="noteref"` + `-cr-hint`。
- 桌面下载器未接 `footnotes.lua`（W13）：注角标转可点由本功能在导出侧实现，
  但**章末注正文保留**，不删。

---

## 二、架构速记

```text
WeReadDesktop.exe (GUI, customtkinter)
   │  子进程 spawn（Tk 线程外，窗口不卡）
   ▼
WeReadWorker.exe (CLI 引擎)
   │  weread_desktop.py
   │   ├─ load_credentials()      ← 两文件凭证契约（lua 基础 + DPAPI overlay）
   │   ├─ WeRead.gateway()        ← api_key Bearer → i.weread.qq.com 网关（搜索/书架/书信息）
   │   ├─ reader_state()          ← weread.qq.com 网页会话（wr_skey cookie）拿 psvts
   │   ├─ chapter_html()          ← 章节分片下载 + LuaJIT decode_shards（MD5 校验）
   │   ├─ download_remote_images()← 章节内 src 引用图片下载（★ 补丁点）
   │   └─ export_book()           ← EPUB 打包 + weread.zip + manifest；缺章拒绝发布
   ▼
微信读书服务端
   ├─ i.weread.qq.com/api/agent/gateway   ← api_key 通道（不带 cookie）
   └─ weread.qq.com/web/...               ← 网页会话通道（cookie；限流 426 在这层）
```

关键契约：
- `load_credentials()` 要求 lua 基础文件含 `api_key` **且** 合并 overlay 后有
  `wr_skey`，缺一即 "Private credentials need an API key and WeRead session"。
- `fetch_book()` 里目录接口（`/web/book/chapterInfos`）返回 200 + errCode -2010 =
  网页会话过期（api_key 通道仍正常，所以 `--check-auth` 会通过——两者不是一回事）。
- `export_book()` 末尾若有 unresolved 章节，**拒绝打包 EPUB**（fail-closed），
  报 `N chapter(s) remain unresolved; checkpoint is retained`，重跑续传补齐。

---

## 三、功能演进时间线

```text
2026-09-26  WeRead 桌面端下载原型（weread_desktop.py 引擎 + bundle 导出）
2026-09-27  搜索乱码修复；窗口化双程序打包；UID 查漏续传
2026-09-28  扫码登录（weread_login.py）+ 网页界面模式 + Inno Setup 安装器
2026-09-30  GUI busy-refusal；脏数据/封面崩溃修复；打包瘦身
2026-10-01  登录表单 fullscreen 互斥修复
2026-10-02  网页版独立 EXE（WeReadWeb）
2026-10-11  划线全点不开三连修 + 评论串书（Kindle 侧插件）
2026-10-12  图形版 UI 全面重排 + ACL 韧性；「是否导出想法」开关
2026-10-13  全套图标；安装包重打；worker 凭证交接修复（零划线真因）
2026-10-14  首运行向导从桩到真实现；手动登录核心缺陷修复
2026-10-15  安装包 1.0.2（内联凭证卡/组件多选）；仓库迁至 各类\weread-downloader；
            weread-importer.koplugin 迷你导入器
2026-10-15  ★ 本日两轮（详见 §四）：① 凭证文件丢失后从 KOReader 备份恢复 api_key；
            ② 定位 note.png 死链图片拖慢下载 ~10 倍，在工作区副本打缓存补丁
```

---

## 四、变更日志（Keep a Changelog 格式）

### 2026-10-21 死链图片缓存跨章节/跨进程复用（**Changed，性能**）

**Changed 同一张死图整本书只问一次**

- **原行为**：403/404/410 的去重表是 `download_remote_images()` 的局部变量，每换一章
  就重建一次，于是 note.png 这类角标图**每章各发一次 404**（后汉书实测 6930 次）；
  进程一退出缓存也没了，续传时从零再问一遍。
- **现在**：进程级 `_DEAD_IMAGE_URLS`（线程安全）负责同一次运行内跨章节去重；
  工作目录 `dead-images.json` 负责跨进程（续传、重跑）去重，`export_book` 开始加载、
  结束（含异常路径）写回。缓存只记 URL，不记状态码。
- **报告不受影响**：命中缓存的那一章仍然把这张图记进 `dropped_images` / manifest，
  只是不再发请求——"少了一张图"不会因为缓存而变成静默损失。
- **测试**：`tests/test_weread_images.py` 新增 4 个用例——三章同 URL 只发 1 次请求且每章
  仍报告；注角标走缓存仍保留 alt 与 `src=""`；缓存落盘/重启往返；损坏的缓存文件按空处理。
  两个既有用例原本共用 `https://x/y.png`，加了进程级缓存后会互相污染，已改用不同 URL。

### 2026-10-21 下载选择改为「注释和想法」默认档 + 附录样式表修复（**Changed / Fixed**）

**Changed（产品规则）标注下拉框换成参考书的形态**

- 用户要求「下载选择里的选择改成这本书的形态」。参考书
  `后汉书全本全注全译（全十卷）-统一附录-v2-前40章.epub` 经比对就是本实现自身的产物，
  所以引擎侧不需要改，缺的是 GUI：
  - 下拉框 `划线 + 想法 / 只要划线 / 纯正文` → **`注释和想法 / 注释 / 想法 / 纯正文`**，
    默认第一项。
  - 四档映射：`注释和想法`(both) / `注释`(notes，`--no-thoughts` + 只收注) /
    `想法`(thoughts，只收想法) / `纯正文`(无附录)。`_queue_args()` 会追加
    `--unified-appendix --appendix-scope <scope>`；`probe()` 不追加（首章检查不写书）。
  - **GUI 并发选择器删除**：实测 jobs>1 更容易触发 426 限流，而队列本身串行、
    单本下载也没有并发收益。worker 固定按 `--jobs 1` 启动（`WORKER_JOBS = 1`），
    GUI 不再暴露该参数；CLI 仍保留 `--jobs` 供高级用法。标注下拉框改为占据
    原来并发框的位置，样式与它一致（圆角、卡片底色、下拉列表同色、无额外描边）。
  - 附录侧新增 `build_appendix(..., include_notes=, include_thoughts=)`：关掉的那一类
    **不参与正文改写**，避免「角标指向不存在的附录页」——「想法」档下注角标保持下载器原样，
    「注释」档下不生成想法角标。
  - 档位定义收敛成模块级 `_ANNOTATION_MODES`，下拉框选项、默认值、worker 参数全部由它派生；
    `_annotation_mode()` 遇到未知标签回退默认档而不是抛错（W3 的教训）。
- **修掉 GUI 从未传过 `--unified-appendix`**：此前这个开关只存在于 CLI，GUI 下载永远产出
  内嵌式（无附录）EPUB。

**Fixed 下拉框开机永远不可选**

- `__init__` 里构造完立即 `configure(state="disabled")`，而全文件只有 `_handle_done()` 会把
  它恢复成 readonly —— 也就是**首次下载之前用户根本改不了标注模式**。改为构造即可选；
  `_handle_spawn_failed()` 里也恢复，避免一次启动失败后永久锁死。

**Fixed 附录页样式表路径解析不到（参考书同款缺陷）**

- 附录文件在 `OEBPS/text/`，原实现写 `href="style.css"` → 指向不存在的
  `OEBPS/text/style.css`，阅读器里 `.wr-ap-*` 全部失效（章节标题没有分隔线、回链不是蓝色）。
  改为 `../style.css`，与正文页一致。**注意：这让输出与参考书不再逐字节相同，是有意修正。**

**Fixed（真机实测发现）两个只在安装包里才暴露的缺陷**

1. **打包漏模块**：`export_book()` 在 `if unified_appendix:` 分支里才
   `from unified_appendix import ...`，PyInstaller 静态分析看不见，`WeReadWorker.spec`
   的 hiddenimports 也没写。结果：汉书 171 章全部下完、到打包阶段才
   `ModuleNotFoundError: No module named 'unified_appendix'`。已在 spec 里显式声明。
2. **队列在失败时不续跑**：续跑代码写在 `elif action == "export"` 分支里，而失败会走
   最前面的 `if self.last_failure` 分支，永远到不了那段——汉书失败后队列里的三字经
   一直没开始。已把续跑移到 else 链之外，`action == "export"` 且 `queue_running` 时
   无论成败都接下一本，并在状态里注明"上一本未完成，断点已保留"。
   同时修正 `queue_running` 的收尾时机：最后一本还在跑时保持 True，跑完才置 False
   并显示"队列已全部完成"（原来的写法在 pop 完最后一本时立刻置 False，完成提示
   永远不会出现）。

**Fixed 队列选中的书详情永远不加载 / 分类永久「加载中」**

- 队列起书为避免与下载抢 worker，`_select_book(load_info=False)` 跳过了读详情，
  但当时把 `pending_book` 写成 `None`——面板永远停在「下载开始后加载」。
  改为记进 `pending_book`，由 `_handle_done()` 的 finally 在空闲时补加载。
- 搜索结束仍没拿到分类的卡片，选中后 `_render_page()` 重建会回到「分类加载中…」；
  `_finish_card_categories()` 的结果没有写回 `book_rows`。现在降级结果写回数据，
  并加 `search_done` 状态位：只有搜索进行中才显示「分类加载中…」。

**Fixed 长标题卡片的「加入队列」被裁掉**

- 勾选框原来 pack 到卡片 inner 的剩余空隙里（cover 和文字列都是 side="left"），
  标题一长就被挤到卡片右缘、被滚动条盖住，只剩半个圆圈。改为放进会伸展的文字列，
  跟在评分下面左对齐。
- 回归测试不只断言「在卡片内」——旧实现也满足这一点；改为断言它落在卡片左侧
  60% 区域内（实测旧实现比值 >0.6，修复后约 0.12）。

### 2026-10-21 (2) 设计审查后的整改（**Changed / Fixed**）

用户要求从设计角度找问题并给出方案，批准后按批次落地。第一批（止损）已完成：

- **删除网页版**：`weread_web.py` / `webui.html` / `WeReadWeb.spec` / 相关测试全部移除。
  它在 `webui.html` 里有脚本语法错误（`node --check` 报 Unexpected token），整页无响应，
  且桌面早已不装它。安装包里的 `UninstallDelete {app}\WeReadWeb` 保留，用于清理
  1.0.2/1.0.3 装过网页版的老机器。
- **暂停语义**：队列里点「暂停下载」现在停当前这本、队列原地不动（暂停时把书插回队首），
  按钮变「继续队列」；原来只是杀 worker，`_handle_done` 会立刻接下一本——与确认框
  「可以点暂停停在当前这本，队列会保留」的承诺相反。
- **退出登录三选一**：`是/否` 改为「仅退出会话 / 彻底清除 / 取消」。原来文案承诺
  「否＝只删会话文件」，代码里选「否」直接 return，什么都没做。现在只有「彻底清除」
  才删 `weread-live-settings.lua`。
- **取消勾选＝出队**：未开始直接从队列移除；运行中标记跳过（重新勾选可撤销）。
  原来取消勾选只改一行状态，书照样下载。
- **失败留存 + 重试**：FAILED 的友好原因存进 `last_failure_status`，选书/搜索都不再覆盖，
  task row 出现「重试」按钮，起新任务后清除。
- **删掉顶部三张统计卡**：已导出书籍/累计划线/下载中 从未被写入，永远显示 `—` 和 `0`。
- **划线清单改读 manifest**：引擎在 manifest 的 omissions 里新增
  `unplaced_underline_total` 与 `unplaced_underline_details`（按章记录），页面直接读成品，
  不再解析 `desktop-general.log`（那里面永远不会出现各书的划线决定）。
- **标注下拉可读**：加「标注方式」标签，并在右侧显示当前档位的后果
  （如「正文只留角标，注释与想法收进书末附录（文件明显变大）」）。

- **队列有了常驻入口**：顶栏「队列 N 本」（运行中/已暂停会标注），点开可逐本移除、
  清空。原来队列只存在于内存和一行状态文字里。
- **队列结果汇总**：每本结束记录 (书名, 成败, 原因)；队列结束时若有书没完成，
  状态行写「N 本成功，M 本未完成」并弹出未完成清单，不再一律显示"全部完成"。
- **下载中控件置灰**：搜索/探针/下载/队列/登录在任务运行时变灰（页面浏览仍可切换），
  不再"看着可点、点了只丢一句拒绝"。

- **附录一致性校验改成真的**：原来的 R6 是恒真式（孤儿定义为"有条目没角标"，
  `条目 − 孤儿 == 角标` 永远成立）。现在逐条解析：每个角标的 `href` 必须落到一个
  已写出的附录文件、且该文件里真有这个 id；注与想法条目数也必须对得上。任一处
  对不上就抛 `AppendixError`，不产残缺 EPUB。
- **引文来源修正**：组级 `abstract` 在真实数据里经常为空，引文只在
  `pageReviews[].review.abstract/markText` 上，导致孤儿条目 100% 没有引文。现在按
  「组级 → pageReviews」回落。
- **孤儿想法单独成节**：没有正文角标的条目不再混在章节里（它们没有可点的 [N]），
  统一收进「未能在原文定位的想法（N 条）」，每条带章名。

未做：**切档不重下（B12）暂缓**。实测确认正文缓存本身是按模式注入过的产品
（`inject` 的结果落在 `chapter-uid-*.body`），只把 reviews 存全量并不够；要做到
切档零重下，必须把标注注入整体移到打包阶段，属于结构性改造，不宜在发版前动。
当前行为：标注模式变化仍会刷新断点并重下（界面会给出提示）。

- **第四批：页面收敛**。「统计分析」改名「存储占用」——它显示的本来就是 exports
  目录占用与可回收空间，和书架页那条横幅是同一件事，旧名字承诺的阅读统计并不存在。
  设置页补了「标注方式」说明，并把退出登录的说明改成与实际弹窗一致（仅退出会话 /
  彻底清除两个范围）。

- **真实语料 gate 不再绑死一本书**。`test_real_corpus_placement_still_verifies`
  原来写死 BOOK_ID=19480992，用户清一次导出目录就会在代码没变的情况下变红
  （2026-10-21 实测红过）。现在优先用它，不在时按划线数量挑一本仍有导出的书，
  并在输出里写明实际用的是哪本；一本带划线的都没有才失败，不静默跳过。

未做（用户已定）：附录分册策略与默认档不改。

### 2026-10-22 界面收敛（第二轮，**Changed / Fixed**）

用户反馈：顶栏「一堆队列什么的」太挤、下半屏空一大片、每次点击都闪一层层加载。

- **顶栏只留三件事**：搜索框 + 搜索、账号、登录状态。移除「加入队列」「开始队列下载」
  两个实心绿按钮和「刷新」（原来七个控件，125% 缩放下最后一个被压成 40 px 只露半个字）。
  - 「加入队列」下移到搜索结果卡片：跟着勾选数显示「加入队列 N 本」，0 本时置灰。
  - 「开始队列下载」移到队列面板底部；运行中显示「下载中…」并禁用，暂停时变「继续队列」。
  - 「刷新」并入状态牌：那块「尚未检查 / 登录有效 / 需要登录」本身就是按钮，点一下重查。
- **设置页补「版本与更新」**：显示当前版本，检查更新按钮从顶栏搬到这里（使用频率最低）。
- **下半屏不再空着**：工作区卡片区高度按窗口高度反算（下限 340、按 DPI 换算成逻辑像素），
  并关掉 `body` 的 `grid_propagate`——不然 `configure(height=)` 根本不生效，卡片区永远
  只长到内容那么高。只绑 `root` 的 `<Configure>` 再补一次 `after(250)`：绑在滚动视口上会
  形成「改高度→重排→再触发」的循环，实测直接把解释器打崩（Segmentation fault）。
- **翻页条被挤没**：结果列表 `expand` 且请求高度很大时，后打包的翻页条只剩 1x1
  （4 页结果时整条消失）。改为 `pack(after=queue_row)`，让翻页条排在列表之前。
- **点击不再层层加载**：选中只刷两处颜色（卡片底色 + 左侧绿条），不再整页重建卡片；
  点已选中的书直接返回，不重复起 `--book-info` worker；详情占位从四行「加载中」减到
  一句「正在读取详情…」。
- **侧栏「设置」点不到**：分隔线原来打进 `side`，在 `nav`（expand）之后改它的高度，
  Tk 把行上移而 CustomTkinter 按钮画布留在原位——文字在 y≈355、命中区在 y≈322，
  点字没反应。分隔线改放进 `nav`，并加测试守住「nav 之后不许再往 sidebar 里塞控件」。
- **队列里弹窗卡住队列**：带「缺少划线」说明的书下完会弹模态，`_ask()` 阻塞主循环，
  而续跑代码就在其后——队列停在「运行中」，下一本永远不开始。队列运行时说明只进
  日志和状态栏。
- **详情行右侧截断**：`wraplength=540` 在 125% 缩放下（675 px）比列还宽，
  「出版社: 中信出版集团 · 出」被切掉。改为按列宽 `<Configure>` 动态设置。
- **出版时间去零头**：接口给 `2018-04-01 00:00:00`，只显示日期。
- **在别的页面搜索自动切回下载工坊**：结果只在工作区出现，留在原页会以为没反应。
- **队列面板按书数收缩**：1 本时不再拉出一个大白框。
- **状态行会换行**：汉书下载完的「152 张插图被删、2537 条划线无位置」整段说明
  原来没有 wraplength，标签按整行文字请求宽度，把 ③ 面板撑出窗口、右侧按钮被
  推出屏幕；现在按窗口宽度换行。
- **删掉「划线清单」页**（用户 2026-10-22：可有可无）。未定位划线的数据仍写进成品
  manifest 的 omissions，下载完成提示与技术日志照常报告；页面本身连同导航项、
  `_read_annotation_notes` 一并移除，导航收敛为五页。
- **完成说明把注角标说成了插图**：汉书提示「152 张插图（分布在 152 章）」，实际是每章
  一个 note.png 注角标（微信读书已删除该文件，注文从 alt 保留）。引擎新增
  `images_dropped_note_markers` 分开计数，旧 manifest 没有该字段时按示例反推；
  文案改为「注角标图标 N 处」。
- **说明框排版**：改用自绘对话框（左对齐、按宽度换行），messagebox 的窄行和
  「·」列表换行不再挤在一起。③ 面板状态行的 wraplength 之前按物理像素设置，
  CTk 又乘了 1.25，长状态文字仍把面板撑出窗口；现在按逻辑像素换算。
- **语料 gate**：所有可用导出都带「注释和想法」附录时，正文被改写过、原 range
  不再对应这些字符，重放测的是错的文本；此时带明确原因 skip，纯正文导出可恢复。
- **快速拖动滚动条时文字错位**：Tk 的 Canvas 滚动按像素搬动可见区域再补画边缘，
  偏移落在半像素上时字形行错位，看起来像乱码。所有列表的 canvas 设
  `yscrollincrement=24`，每一步都落在整像素上。

### 2026-10-22 搜索结果与书架改为 canvas 绘制（**Changed，滚动**）

快速拖动滚动条时卡片叠影，前后试过三种滚动方式都不彻底：CTkScrollableFrame 的
Canvas.yview 是「先贴背景、再移窗口项」两步；place 移动内容框时 Tk 帧不裁剪子窗口，
滚出区域的卡片会画到外面；coords 移动窗口项又会留下未重画的旧像素。

- **根因**：卡片是子窗口，而 Tk 的 canvas 滚动对子窗口永远存在「背景已动、窗口未动」
  的时间差。
- **现在**：搜索结果与微信书架整行画在 canvas 上（背景块、左侧绿条、封面图片、
  标题/作者/分类/评分文字、勾选框与「下载」按钮都是 canvas item），滚动时整张画布
  一起搬，不存在子窗口滞后。点击按行坐标命中，勾选/下载行为不变。
- 同步更新测试：卡片断言改读 canvas item 文本；书架测试改读 canvas 文本与
  show_empty()。

### 2026-10-22 大图把整章拖成 WinError 32（**Fixed**）

用户日志（`desktop-3300227845.txt`，`D:\WeReadDownloader` 旧版）：《北魏王朝》23 章里 4 章（28/31/35/39）每次都在 `extract_images → _save_image_stream` 失败，重试 8 次仍 4 章未完成，书被拒绝出版。

- **根因**：旧代码对「超过 16 MiB 的图片」做清理时，把 `target.unlink()` 写在 `with target.open("wb") as output:` 里面——文件还开着就删，Windows 必然报 `WinError 32 另一个程序正在使用此文件`。本该报「图片过大」的分支，反而变成权限错误，章节被标 INCOMPLETE；那个超大文件还留在 `.work\images`，下一次重试按已用名分配成 `-2.jpg`、`-3.jpg`…… 越攒越多（日志里到 `-8`）。
- **现在**：单张上限 16 → 64 MiB（包内条目 128 MiB、整包 512 MiB）；超过上限按「缺一张图」处理——跳过、删掉 `src=""` 的 `<img>`、计入 omissions，章节照常完成、书正常出版，完成提示会说明少了几张图。
- 临时文件的删除/替换加了短暂重试（杀毒或索引器短暂占用同样会 WinError 32），清理失败不再连累章节。
- 队列结束弹窗（同批用户的截图）是 10-21 加的汇总：只要有队列书没完成就会列书名与原因；「下载没有完成」是未知失败的兜底文案，不是新错误。

### 2026-10-22 常驻 worker：一次启动，多次复用（**Changed / 架构**）

用户反馈"为什么获取这么慢"。实测把原因拆开了：

| 场景 | 耗时 |
| --- | --- |
| 命令行直接跑 `WeReadWorker.exe --book-info` | 0.8–0.9 s（引擎 + 一次 HTTP） |
| 冷启动 worker（LuaJIT + 3000+ 插件文件） | 约 0.6–1 s |
| 每次覆盖安装后第一次启动 | 20–28 s（Defender 扫描新 exe；日志里的 `[SPAWN] … took 23–28 s`） |

改动：

- 引擎新增 `--serve`：从 stdin 一次读一行 JSON 请求，复用**同一个** LuaJIT
  （`lua_runtime()` 加缓存），凭据每个请求重新读取，登录后立刻生效。
  输出统一分帧成 `{"id": n, "line": "..."}`，书名的引号/制表符不会和协议混淆。
- GUI 新增 `_ServiceClient`：常驻一份 worker，负责 auth / shelf / search /
  book-info / probe；导出和扫码登录仍各自起独立进程（要能暂停、要交互）。
  服务不可用或中途退出时自动退回原来的"按需起进程"，功能不受影响。
- 常驻请求没有 `process` 可判活，新增 `_service_job` 记当前 job，避免
  同一个窗口叠两个任务。
- stdin/stdout 强制 UTF-8（`PYTHONIOENCODING` + `sys.stdin.reconfigure`），
  否则子进程按 cp936 读请求，中文关键词和书名会变乱码。
- 实测（warm）：3 次 book-info 1.41 s vs 3 个进程 2.21 s；单次点击从约 1 s
  降到约 0.3–0.5 s（主要剩网络）。

### 2026-10-22 插图按内容去重（**Fixed，体积**）

用户问"为什么有的书会涨 330 兆"。拆开汉书（3300185594）的导出目录：

| 组成 | 大小 |
| --- | --- |
| `… - full.epub` | 199 MB（其中 737 张 JPG 占 185 MB、附录 XHTML 43 MB） |
| `… - full.weread.zip` | 114 MB（**内含同一份 EPUB**，是给设备导入用的包） |
| `reviews.jsonl` | 19 MB（想法原始数据） |
| `.work/`（断点目录，另计） | 261 MB（章节正文 + 已下载图片，供续传） |

EPUB 里的图片再拆：**1569 个条目、686 张唯一图片**，按 CRC 统计重复 **94 MB**。
原因是同一张图有两条来源——章节图片包（tar）一份、正文远程 URL 再一份，旧代码按
"章节号-文件名"各写一份；`note.png` 这类角标甚至存了 152 份。

- 现在按 sha256 复用：第二个来源命中同一内容时直接引用已有文件名，不再落盘
  （`_save_image` / `_save_image_stream` / 远程下载三条路径都走同一张表）。
- **已经导出的书不会自动变小**，要重新导出才会应用；现有目录里的重复文件保持原样。
- **已导出的书也能瘦**：`tools/slim_epub.py <epub>` 按内容去掉重复图片、把正文引用改到
  留下的那一份，改写前逐条校验引用、校验通过才原子替换原文件。汉书实测
  198.1 MB → 103.7 MB，正文（归一化图片引用后）逐字节一致。
- **导入包已停用**（用户 2026-10-22 明确：不需要导入 Kindle，只要一个 EPUB）。
  同一本 EPUB 再打包一份成 `.weread.zip`，大书白白多占 114 MB。旧文件保留在磁盘上，
  由「存储占用」页清理。

**真机验证（Windows，安装包 1.0.5）**

- 全程用 GUI 点击：搜索「汉书」→ 选中 → 勾选「加入队列」→ 搜「三字经」→ 勾选 →
  加入队列 → 开始队列下载 → 确认。
- 汉书 171/171 章下载完成，打包成功：21 册注（69,322 条）+ 1 册想法（6,815 条），
  EPUB 198 MB，`text_complete: true`，附录样式 `../style.css`。
- 汉书成功后队列自动接三字经（复用缓存，34 条想法），界面最终显示「队列已全部完成」。
- 安装与凭证：覆盖安装不动 `%LOCALAPPDATA%\WeReadDesktop`，两个凭证文件原样保留，
  已安装的 worker `--check-auth` 输出 `AUTH_OK`。

**验证**

- `tests/test_desktop_annotation_modes.py` 重写：三档 × probe/export 的参数断言、
  默认档断言、未知标签回退断言；新增需要 exec `_AnnotationMode` 与档位表。
- `tests/test_desktop_queue.py` 4 个用例的期望参数补 `--unified-appendix`。
- 真引擎端到端（三字经 11 章全缓存）：`--unified-appendix` 复用 11 章、0 重下，产物含
  `appendix-thoughts.xhtml`（34 条），manifest `appendix.thoughts=34`。
- 从同一份缓存重建的 40 章后汉书与参考书逐项对齐：附录 5 文件、nav 标签
  （`注释` / `注释（续）×3` / `想法`）、条目 id（`note-<uid>-<n>` / `wrfn-<uid>-<s>-<e>`）、
  回链形态全部一致；仅样式表路径按上面修掉。

**已知未做 / 待决定**

- Web 面板（`weread_web.py` + `webui.html`）仍是两态「含想法」开关，未接附录；两个前端的
  默认值目前不一致（desktop 默认含想法+附录，web 默认不含想法），要么一起改、要么明确 web 不在范围内。
- 参考书里的若干内部不一致随实现保留：跨段划线产生重复 `wrbadge-*` id（EPUBCheck 级错误）、
  无注章节仍输出空 `<h2>`、导航标签用「（续）」而页面标题是「（1/4）」、导语写「[注n]」但
  角标只有「注」。这些不是本次改动的回归，已在 §五 登记。

### 2026-10-20 统一附录（注释和想法）功能实现（**Added，默认关闭**）

**Fixed（严重）注在前 29 章完全丢失：死链清正则把注角标当图片删掉（W13 根因）**

- **现象**：生成的 EPUB 前 29 章一个「注」都没有（版权/导读/凡例/卷一~卷九帝纪），
  但微信读书原书这些章有注。
- **根因**：注角标是 `<img class="qqreader-footnote" alt="注文" src="note.png">`，
  note.png 服务端 404；`download_remote_images` 对 404 的图返回空串删掉 `<img>`，
  随后清理正则又把空 src 的 img 全删——**注文在 alt 里，图丢了注也没了**。
  后汉书 35 章、数千条注被整章删除（`dropped_images` 每章 400+）。
- **修复**：
  1. 404 判定时，若该 img 是 `qqreader-footnote`，只清空 `src`、保留标签（alt 留着）；
  2. 清理空 src 的正则加负向前瞻 `(?![^>]*qqreader-footnote)`，不再误删注角标。
- **验证**：重新下载前 40 章后，注角标从 0 恢复到 **10,897 个**（uid-10 426、uid-17 458、
  uid-30 431），正文角标 ↔ 附录注条目一一对应。
- **目录**：章名改用 `manifest.json` 的 `chapterUid→title` 映射（缺章不错位），
  显示「卷一上 光武帝纪第一上」等真实章名，不再是 `chapter-uid-N`。

**Fixed（高）注在阅读器里"看不见"——死图注角标转可点 [注n] + 注文进附录**

- **现象**：旧产物（build_hhs_real.py）实测"想法能看、注释不见了"。拆包发现：
  真正的注（`<img class="qqreader-footnote" alt="注文"/>`，36,185 个）是**无 src 的死图**，
  阅读器显示不出来；旧产物反而把想法当"注"塞进分册，真注全部丢失。
- **修复**：`rewrite_notes()` 把死图角标转成 `<span class="wr-note-ref"><a epub:type="noteref"
  href="#note-<uid>-<n>" id="notebadge-…">[注n]</a></span>`，注文从 `alt`（HTML 字符引用）
  `unescape` 后收进附录；角标文字用「注」（不带数字）；alt 为空的角标整段删除，不留死图。
- **分册**：注按 ~3000 条/册拆分（后汉书 36,185 注 → 12 册，0.37–1.32 MB/册），
  想法单独一册（307 KB）——单文件 12.2 MB 实测会卡。
- **验证**：后汉书 155 章生成 7.8 MB EPUB，正文注角标 36,185 == 附录注条目 36,185；
  三字经 0 注/34 想法正常。EPUB 在 `想法注释内嵌尝试/测试产物/`。

**Added**（**中**——把一本书的全部想法收敛到书末一个独立章节，正文只留角标）

- **接口**：新 CLI 开关 `--unified-appendix`（默认关）。打开后：
  - 书末追加单个 `text/appendix.xhtml`「注释和想法」章节（nav/spine/opf 同步登记）；
  - 正文 `<a id="wrthought-…" href="#wrthought-…">` 成对改写为 `href="#wrfn-<uid>-<range>"`
    + `id="wrbadge-<uid>-<range>"`，角标点开跳附录条目、条目编号跳回原文；
  - WeRead 原生 `[N]` 计数角标移除（统一角标已表达条数）；
  - 注**不**进附录（R3：注量级是想法的约 38 倍，注保留章末）；
  - manifest.json 新增 `appendix` 块（notes/thoughts/thought_badges/chapters/notes_parts/
    orphan_entries/unlinked_underlines）。
- **实现**：新模块 `weread/unified_appendix.py`（load_reviews / rewrite_badges / build_appendix_xhtml
  / build_appendix），`write_epub()` 加可选 `appendix` 参数；`export_book()` 加
  `unified_appendix` 形参。reviews.jsonl 的汇总**前移到 write_epub 之前**（附录与 bundle 共用）。
- **数据口径（实测后汉书 155 章 / 三字经 11 章）**：
  - **被改写角标必须在附录有落点**（硬断言，不一致抛 `AppendixError`，不产点开是空的 EPUB）；
  - 有条目无角标（划线注入失败，WeRead 引文匹配不上）→ 条目保留、记 `orphan_entries`、不放假回链；
  - 有链接无想法组（只划线没写想法）→ 链接原样保留、记 `unlinked_underlines`；
  - 同一 range 多个 review 组（WeRead 分页）→ **合并 items**，不覆盖（否则静默丢整组想法）。
- **验证**：`tests/test_unified_appendix.py` 11 例全过；三字经真实语料 e2e 通过
  （角标 33 == 锚点 33、回链 33 == 正文 id 33、orphan 1 如实上报）；后汉书 155 章构建成功
  （条目 798 / 角标 506 / orphan 292 / unlinked 17，附录单文件 313.8 KB）。
- **与 W12 的关系**：附录是**单文件**——旧「分册」路线（124 册 11 MB）实测卡死 Calibre，
  单文件 314 KB 在 Calibre 与 KOReader 能力内。
- **状态**：功能完成、测试通过；**尚未真机（KOReader/Calibre）打开验证**，待用户实测。
  工作副本：`Kindle\07_试验区\统一附录功能-20261020\weread-downloader\`（未合入仓库、未提交）。

### 2026-10-16 研究主题：注想法弹窗统一 / 注角标可点击 / 想法内嵌式 / Calibre 附录承载边界（**仅调研，未改仓库代码**）

**Changed（调研结论，来自 `Kindle\07_试验区\` 四个主题；2026-10-16 ~ 10-17，均为只读调研）**

- **注想法弹窗统一（2026-10-16）**：源码级核查 KOReader/crengine——`<details>`/`summary` 在 crengine 全量 0 实现（折叠只有块级、无 open 属性/点击切换），CSS `:target` 在 KOReader frontend 全量 0 命中；但 crengine 私有的 `-cr-hint: noteref/footnote` 可强制走脚注弹窗（官方 `readerstyletweak.lua` 说明）。结论：纯 EPUB + `-cr-hint` 在 KOReader 上能做到「点开弹窗 + 翻页」；弹窗内容按「一个目标元素」取，**多条想法要写进同一 aside 由引擎滚动/分页**，不似插件自绘弹窗可精确分页。
- **注角标可点击（2026-10-16）**：插件 `footnotes.lua` 已是完整实现，设备端整书下载的书注角标本就可点；桌面下载器链路未接。PoC 用 lupa 加载插件 `footnotes.lua` 跑真实章节，汉书/后汉书 203/476/585 处注全部转换成功、悬空链接 0；并定位一处真缺陷——清洗正则 `(?is)<img\b[^>]*/>` 在有死链图时会把**全章自闭合 img（含注角标 `alt`）整章删掉**（汉书 26 章、后汉书 35 章注被误删）。
- **想法内嵌式（2026-10-16）**：成品 EPUB 里想法正文 0 处（只在 `thoughts.db` 旁路），用 lupa 在插件 `annotations.lua` 注入处加一处即可让桌面/设备两条链路同时内嵌。PoC 实测：小书（三字经 96 章/431 划线）全量内嵌仅 **+0.39%（106 KB / 27.5 MB）**，标记 delta 全 0、XML 良构；大书（《北派盗墓笔记》1839 章/61,249 条想法）按 31% 压缩率推算 top-all 约 **+54%**。用户已拍板全量内嵌、旧书不重做、目标场景为电脑 EPUB 阅读器。
- **Calibre 附录承载边界（2026-10-16/10-19）**：用真实书籍数据（`capacity_probe.py`）合成「注+想法统一附录」测容量，结论——**Calibre 的可渲染硬上限在「XHTML 文件总数」约 110–180 个**，与附录字节数无关（三字经 103 XHTML/1MB 可渲染；后汉书 185–306 XHTML/6MB 稳定卡 `70% Rendering N HTML files`）。短中篇（三字经/逆流前 100 章）可附录化，长篇（逆流全本 799 章/6532 想法、后汉书全本 155 章/36870 注）超文件数上限。注的量级是想法的 38 倍，建议附录只放想法。

---

### 2026-10-16 (3) W10：`process_is_alive` 在 Windows 上给自己发 Ctrl+C（**已修复**）

**Fixed**（**中**——影响导出锁的存活判断，不只是测试问题）

- **问题现象**：CI 两次都在跑到约一半时 `KeyboardInterrupt` 中止，各只跑完 188/401 个用例；本机跑全套不出现。
- **根因**：`weread_desktop.py` 的 `process_is_alive()` 用 `os.kill(pid, 0)` 判断进程是否存活。在 Windows 上 `signal.CTRL_C_EVENT` 的整数值就是 0，CPython 因此走 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)` 分支——不是「探测」，而是往该进程组发一个**真的 Ctrl+C**。导出锁 `export.lock` 里记的是上一个导出进程的 pid，当那个 pid 与当前控制台的进程组号重合时（CI 运行器上就会），**检查锁 = 把 Ctrl+C 打给自己**，Python 抛 KeyboardInterrupt，pytest 中止整个运行。
- **修复方案**：Windows 分支改为 `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(handle, 0)`——进程已退出时立即返回 `WAIT_OBJECT_0`，还在跑则返回 `WAIT_TIMEOUT`。打不开句柄时按 `GetLastError` 区分：5（拒绝访问）= 进程在别人手里，保守算活着；其余 = 已消失。非 Windows 保留原来的 `os.kill(pid, 0)`（在 POSIX 上那才是正确的探测）。
- **验证**：活进程/死 pid/起始时间不匹配三种情况逐一验证通过；`tests/test_disk_edge_cases.py` 新增 `TestProcessIsAlive`，其中一条从源码层面断言「Windows 探针函数体里不再出现 `os.kill`」。
- **同批**：`tests/test_desktop_bundle.py` 缺外部插件目录时原先在收集阶段直接 FileNotFoundError，改成 `allow_module_level` 跳过。
- **后续**：该修复引入的「取锁时间被当成进程创建时间」语义混淆由 PR #23 再修一次，见 2026-10-18 (0)。
### 2026-10-18 (1) 上传书显示 19487 章：目录把「注」拆成了独立章节（**已修复**）

**Fixed**（**高**——上传书完全无法导出，且产出的断点会让人以为在下 19487 章）

- **现象**：用户上传到微信读书的个人文件《后汉书前20章-注角标可点开-demo》（`CB_9WJ8WN8Tj9Vk77675X4XM1FS`）在 GUI 里显示「正在下载第 4/19487 个目录章节」。
- **根因（登录后实测）**：上传书的目录把原文夹注拆成了独立章节。19487 条的实际构成：
  - 9722 条 `title=注`（每条 13-30 字，一条夹注一个「章节」）
  - 9765 条 `title=wrfn-89-1` 这种脚注编号（每条 17-134 字）
  - 20 条真正的正文（`第 N 章（uid X）`）
  - 23 条章节续段（`第 N 章（uid X）_1` / `_2`）——**目录标 11-13 字但接口返回空 body**
  这些注的正文本来就嵌在章节正文里（EPUB 的「注」角标就是从这来的），当成章节下载只会产出一个 19487 页、每页几十字的重复文件；续段则每一条都会重试三次后标 INCOMPLETE，最终让整本书 fail-closed 拒绝出版。
- **排查中走过的两次弯路**（记录下来避免重犯）：
  1. 先判断成「上传书的目录天然被切成上万节点」，据此按 `CB_` 前缀拒绝一切上传书——结论错，已由 PR #36 撤销。
  2. 再判断成「过期会话的脏数据」——当时商店书也确实报 `errCode -2012`，但重新扫码登录后商店书恢复正常（23 / 155 章），上传书**仍然**是 19487，这才看清真实结构。
- **修复**：`fetch_book()` 在做其他判断之前先剔两类碎片：
  - `_is_annotation_fragment()`：title 为 `注` 或匹配 `wrfn-<数字>-<数字>`
  - `_is_continuation_fragment()`：title 以 `_<数字>` 结尾
  5000 章上限保留，作为「目录以其他方式坏掉」的最后兜底。
- **端到端验证（真书，非模拟）**：
  ```
  [CATALOG] dropping 19444 annotation fragment(s) (19487 -> 43)
  [CATALOG] dropping 23 continuation fragment(s) (43 -> 20)
  [20/20] Fetching chapter UID 42
  OMISSIONS {"text_complete":true, ...}
  EPUB .../后汉书前20章-注角标可点开-demo - full.epub
  ```
  产物 20 章 + 封面，spine 21 项，0.16 MB。
- **测试**：`tests/test_uploaded_book_catalog.py` 共 12 个用例，覆盖注判据、脚注判据、续段判据、真实章节不误判、空标题不误判、用实测形状重建目录验证最终剩 20 章、坏断点作废、正常断点保留。
### 2026-10-18 (0) 协作者 7 个 PR 全量合入：CI 首次全绿 + 排队下载/自更新上线（**未发版**）

**Added / Fixed**（**高**——CI 从「永远跑不完」变成 425 passed / 0 failed）

- **合入范围**：#23、#26、#29、#31、#33、#21、#27 共 7 个 PR，关闭 issue #22/#24/#25/#28/#30/#32。按协作者建议的顺序合并（#23 → #26 → #29 → #33 → #31，再 #21，最后依赖它的 #27）。
  - #23 导出锁身份：原来把「取得锁的时间」当成「进程创建时间」比较，进程启动超过 5 秒的活锁会被误判失效而被接管（这是 10-16 W10 修复时引入的语义混淆）。新增 `process_started` 字段，并用系统文件锁（`msvcrt.locking` / `fcntl.flock`）串行化旧锁检查、清理与新锁发布，两个竞争调用者不能再互相删掉对方刚拿到的锁。
  - #33 Tk 测试生命周期：pytest 默认 fd 捕获会替换 OS 描述符，干扰 Tcl 读 init.tcl，改成 `--capture=sys`；`DesktopApp` 的 stall-watch 线程在窗口销毁后原来永久循环，现在 `<Destroy>` 里通知退出并 join，同时清掉 `after` 定时回调。`test_page_switch.py` / `test_desktop_stall_watch.py` 从「导入即跑」的脚本改成正常 pytest 用例。
  - #31 GUI 焦点与布局：下拉框点击后焦点被 Canvas 绑定抢回，改到空闲回调里聚焦真实输入框；简介行数断言改成先填四行真实文本再量可见边界；导航点击按 Enter → press → release → Leave 完整事件序验证（CustomTkinter 6.0.0 的合成点击需要补 Enter，否则指针已进入时断言失败）。
  - #29 外部资源边界：真实书籍语料回放加 `local_corpus` 标记，通用 CI 用 `-m "not local_corpus"` 排除；缺 Lua 插件时在 helper 前检查，由既有跳过钩子报告原因，不再误报成业务断言失败。
  - #26 测试隔离：凭据/退出登录用例改为每个用例执行期间绑定自己的临时路径、结束后恢复，修掉收集期共享 `weread_login.CREDENTIAL_STATE` 导致的交叉污染。
  - #21 功能：**排队下载**（结果卡片勾选 + 「加入队列」/「开始队列下载」，跑完自动续下一本）+ **应用内自更新**（查 GitHub Releases → 下载官方 Setup.exe → 提权拉起覆盖安装，不签名、失败静默降级、与下载队列互斥）+ MIT LICENSE 与 THIRD_PARTY_LICENSES.txt 随安装包分发。
  - #27 测试适配：标注模式回归改为沿真实 `_start_one()` / `_queue_args()` 调用链验证参数。
- **CI 首次全绿**：Windows / Python 3.9 作业 **425 passed / 29 skipped / 1 deselected，0 failed**（运行 39104814349，2 分 04 秒 ~ 3 分 15 秒）。privacy gate 持续 13 秒通过。**建议下一步**：把 `tests` 作业从 `continue-on-error` 摘掉，加进 protect-master 的 required status checks——这道门现在才真正有资格关上。
- **遗留分支**：`test/ci-combined-python39` 是协作者的组合验证分支（他自己说明仅用于验证，无汇总 PR），未删除，等所有者决定。
### 2026-10-16 (2) 协作者 8 个 PR 全量合入 + 本仓库测试/CI 首次可跑（**已合并上游 master，未发版**）

**Added / Fixed**（**高**——覆盖 GUI 下载链、断点续传正确性、凭证就绪、书架失败态）

- **合入范围**：协作者 Wu-Wh0 的 8 个 PR（#10–#17）+ 本机实现的测试/CI PR（#18），issue #1–#8 全部关闭；#9 被 #10–#13 取代，关闭未合并。
  - #10 `probe_book(..., want_underlines=...)`：调用方传 5 个参数、签名只收 4 个 →「先检查第一章」必崩（问题 W4）。
  - #11 GUI 标注三模式：默认值 `both` → `划线 + 想法`、下拉框 `state="normal"` → `"readonly"`、模式比较改按中文串、删掉 `("both","marks","plain").index(mode)` 的 ValueError（问题 W3，即下载按钮崩溃）。
  - #12 断点按标注模式隔离：state.json 增写 `want_underlines`，`state.update(old)` 改为兼容性判定（模式不一致即刷新暂存章节，不再混排）。
  - #13 断点校验统一：`discover_checkpoint_files` 用「正文非空」替代 `wr-underline` 计数，`download_one` 用 `(body, review, css)` 三元组比对缓存 uid。
  - #14 扫码成功后刷新凭证卡片；#15 `_credentials_usable` 改为自写 Lua 字面量解析（`first_run._settings_literal`），GUI 就绪判定与 worker 的 `load_credentials` 对齐；#16 书架失败态收尾（`_show_shelf_error` 5 处调用点，不再卡在「加载中」）；#17 `ERRORS_ZH` 补「基础凭证缺失」的明确文案（原来是误导性的下载失败提示）。
- **合入前的实测风险处置**：#12 会作废缺 `want_underlines` 的旧断点（4 本书约 399 章）。按证据逐本补写——三字经、战国策校注 `true`（work 目录有 reviews.jsonl / `wr-underline` 标记），后汉书、汉书 `false`（两者都没有）——备份在 `%TEMP%\weread-state-backup-20261016`；补后 4 本均判定「兼容，断点保留」。
- **协作者此前无法跑测试的根因（本机修好）**：
  - `pytest.ini` 加 `python_functions = test_*`。各测试文件用 `def test(fn)` 装饰器注册用例，pytest 默认模式 `test*` 把这个装饰器本身当用例收集，30 多个文件各报一个 `fixture 'fn' not found`——修掉后全套 386 passed。
  - `tests/conftest.py` + `weread_desktop.py` 插件探测改惰性：仓库外没有 Lua 插件目录时原先 import 即 `RuntimeError`（约 200 行测试跑不了，协作者只能用 AST 抽取绕过），现在按 README 的「宁可降级也不失败」就地 skip。
  - `tests/test_annotation_modes.py` 的写死绝对路径改为 `__file__` 定位。
- **CI**：`.github/workflows/ci.yml` 两任务——`privacy-gate`（ubuntu，阻塞，13–15s 通过）+ `tests`（windows，暂 `continue-on-error`，受 W10 影响）。
- **修复「登出会删真实凭证」（本机踩到，严重）**：`desktop_app.py` 的 `logout()` 里 `from .weread_desktop import SETTINGS` 在 `weread/` 无 `__init__.py` 时必然失败，于是「测试环境 fallback」是唯一会执行的分支，按 `%LOCALAPPDATA%` 手拼路径 → 任何测试重定向都拦不住，跑 `tests/test_desktop_logout.py` 会删掉开发者本人的 `weread-live-settings.lua`。改为统一走 `first_run._settings_path()`（资料目录唯一所有者），测试同时 patch 该路径并加断言。真实凭证已用 `session-cookies.json` + `first_run.build_settings_lua` 还原，`--check-auth` 输出 `AUTH_OK; shelf entries: 7`。

**未做（等决定）**

- W1 死链图片缓存未入仓（`_DEAD_IMAGE_URLS` 只在 `WeReadWorkerPatched` 副本里）。
- W10：`weread_desktop.py::process_is_alive` 的 `os.kill(pid, 0)` 在 Windows 上`signal.CTRL_C_EVENT == 0`，CPython 会转成 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`，等于给整个进程组发 Ctrl+C——CI 两次都在跑到一半时 `KeyboardInterrupt` 中断。
### 2026-10-16 (1) 全新安装扫码登录后不可用：QR 登录从不创建基础凭证 lua（外部用户反馈）（**已修复，v1.0.3 未分发**）

**Fixed**（**严重**——全新用户完全无法使用）

- **问题现象**：外部用户报告，全新安装 + 扫码登录成功后，每个功能都报
  `Credential file not found: …weread-live-settings.lua`，反复出现 5 次以上。
- **根因分析**（代码级证实）：两份凭证的写入路径不对称——`_finish_login`
  （desktop_app.py）扫码成功后**只调** `save_credentials()`（写 DPAPI 加密的
  session-cookies.json overlay）；而引擎必需的基础文件
  `weread-live-settings.lua` 只由两条路径创建：手动输入凭证
  （`_save_manual_credentials` L2894）与首启向导（first_run L406）。全新用户
  走 QR 扫码两条都不经过 → 基础 lua 永远不存在 → `load_credentials()` 抛
  NOT FOUND。讽刺的是 `complete_login()` 的载荷里就带着 api_key——写 base
  所需的一切都在手里，只差一次调用。
- **修复方案**：`_finish_login` 在 `save_credentials` **之前**调
  `first_run.write_settings(_settings_path(), api_key, cookies)`（载荷自带
  api_key），与手动路径完全对称；base 写失败降级（overlay 照存，下次启动给
  具体错误）。模拟验证：全新目录 + QR 载荷 → 两个文件都创建、
  `load_credentials`/`has_credentials` 全通过。
- **同批修复（外部用户 #2）**：登录时 UI 冻结 ~7 秒——
  `_restrict_to_current_user` 的 `whoami`/`icacls` 在 Tk 线程同步执行，杀毒
  软件扫描新写入的会话文件把命令卡 7.2 秒（STALL_STACK 实录）。修复：两个
  子进程调用加 2s `timeout`，`TimeoutExpired` 按 ACL 非致命降级（payload
  本体 DPAPI 加密，ACL 只是纵深防御）。崩溃/卡顿定性为**代码缺陷**；
  用户 #1 早期另见的一次 `unexpected format`（文件存在但损坏）来源未定——
  其机器上出现过本仓库不存在的恢复注释字符串，疑似外部工具，已请用户自查。

### 2026-10-16 (0) 分发物料与文档同步（**Added/Changed**）

**Changed**（**低**）

- `installer/WeReadDesktop.iss` `MyAppVersion` 1.0.2 → 1.0.3（对齐实际发布号）。
- README 登录节重写：QR 登录自动维护两份凭证（v25 行为），
  「手动新建 weread-live-settings.lua」步骤删除；已知限制删除
  「程序从不写它」过时声明；登出文案去掉已废弃的 `user_vid` 字段。
- 「彻底清除凭证」已知限制与「以后也许会做的」同步为 1.0.3 语义。
- 「以后也许会做的」删除「并发可以更高」条目（按用户要求）。
- 新增已知限制：**古文注释「注」角标不支持**（如《后汉书》原文夹注为微信
  读书特殊排版元素，导出 EPUB 仅保留正文）——外部因素（微信读书排版特性
  + CRE 引擎支持度），列入以后调研。

### 2026-10-15 (3) KOReader 独立导入器 weread-importer.koplugin（**开发中，未发布**）

**Added**（配套 Kindle 侧交付；**设备部署细节、FileBrowser+ 端点坑、验证清单见 Kindle 文档 §四 2026-10-16 条目**）

- 独立迷你导入器（17 个 lua，290 KB / 完整版 11%），无账号、不联网：
  把 PC 端导出的导入包（目录形态）装进 KOReader 书库并回贴划线想法。
- 本机 LuaJIT 全链复现环境（shim rapidjson/lfs/sqlite3）打磨出 5 轮修复：
  WidgetContainer 类形态、init 阶段、菜单键赋值模式、零 FFI（设备 libarchive
  在 4 个不同位置硬崩，trace 逐一定位）、data_dir 绝对锚定、
  weread.lua 书记录对齐（cache_dir 指向 weread/annotation-cache/<id>）。
- **状态**：导入链在设备上已跑通（书+划线入库），但下划线点击弹窗、
  SimpleUI 书库封面墙显示两处仍未验收 → 按用户指示**暂不发布**
  （v1.0.3 release 资产已撤下，README 标注开发中）。

### 2026-10-15 (2) 死链图片拖慢整书下载 ~10 倍：工作区副本缓存补丁（**本地副本，未改安装版**）

**Changed**（补丁）/ **_external**（404 本身是服务端行为，不是缺陷）

- **问题现象**：用户报"速度太慢"。实测串行 1.5 分钟才下 1 章；22:37–22:44 双书并行
  8 分钟仅 49 章（两本合计）；后汉书 25→34 章耗时 12 分钟。
- **根因分析**：每章正文嵌 150–190 个 `weread.qq.com/Images/note.png` 图标引用，
  服务端一律 404。`download_remote_images()` 的 URL 去重缓存 `url_to_href` 是**章内
  局部变量**，跨章不复记忆；且 404 分支不 `close()` 流式响应。日志量化：最近 1500 行
  输出里两书各 5–8 章 / 各 ~1490 条 `[IMAGE] dropping (HTTP 404)`，限流/重试 0——
  时间全部耗在对同一批死链的重复请求上。此前的"限流慢"归因被这轮数据推翻（限流
  只在双书并行首 8 分钟出现过）。
- **修复方案**（3 处，`_internal\weread_desktop.py`）：
  1. 模块级 `_DEAD_IMAGE_URLS: set[str]`（进程级死链记忆）；
  2. `replace()` 请求前查集合，命中直接 `return ""`（与 404 分支同样留空 src）；
  3. 403/404/410 分支 `_DEAD_IMAGE_URLS.add(url)` + `response.close()` 后再记日志。
- **为什么是"工作区副本"**：安装位 `C:\Program Files\WeReadDownloader\` 由管理员
  安装器创建，编辑工具与当前 shell 均无写权限（Cannot write / ACCESS DENIED，无法
  提权）。故整包复制到 `C:\Users\asus\dsh-workspace\WeReadWorkerPatched\` 打补丁，
  断点/凭证在 `%LOCALAPPDATA%` 不受影响，与安装版完全兼容。**上游修复建议**：
  把 `_DEAD_IMAGE_URLS` 提交到官方仓库（原版文件 sha8 `C4275775`，补丁版 `9BEC87F6`）。
- **实测数据**：补丁后 3 分钟下 35 章（79/155 + 67/171，两本并行 jobs=2），约合
  补丁前 **10 倍**。《战国策校注》23 章整本两轮完成（第一轮 17/23 + 6 章被 426
  限流保留断点，冷却 60s 后续传全齐，`text_complete: true` 出版）。
- **验证**：`--check-auth` 冒烟通过（LuaJIT 加载补丁文件无语法错误）；产物
  `战国策校注 - full.epub` 0.46 MB 已交付桌面。

### 2026-10-15 (1) 凭证文件丢失 → "Credential file not found"：从 KOReader 备份恢复 api_key

**Fixed**（本机事故恢复；上游缺陷另列问题 W2）

- **问题现象**：GUI 任何操作报 `FAILED RuntimeError Credential file not found:
  ...\weread-live-settings.lua`，反复出现（desktop-general.log 多条）。
- **根因分析**：`load_credentials()` 把该 lua 文件当作**必需基础文件**（上游 10-14
  条目所述契约）。本机曾于 0:44 成功导出《三字经》说明当时文件在，之后被删除
  （22:09 扫码登录成功瞬间 `session-cookies.json` 也短暂存在后消失——用户操作了
  「退出登录」/「彻底清除凭证」的可能性最大，两者都会删文件）。
- **恢复方案**：在 `C:\Users\asus\Desktop\各类\Kindle\07_日志与诊断快照\weread-live-settings.lua`
  （KOReader 插件 9-10 的设置快照，同账号）里找到 `api_key`（`wrk-…`）与当时 cookies，
  按加载器解析格式重建 `weread-live-settings.lua`（`\nreturn {…}` 表；cookies 键为
  明文字符串）。9 月的 wr_skey 已过期属预期——overlay 会话由扫码刷新。
- **验证**：`--check-auth` → `AUTH_OK; shelf entries: 6`。注意此命令只验证 api_key
  网关通道；**网页会话（wr_skey）过期要在章节目录接口才暴露**（errCode -2010
  "用户不存在/未登录"），见 §6.5 第 3 行。

### 2026-10-15 (0) GUI「开始下载/先检查第一章」按钮坏：临时以 CLI 绕过（未修，登记）

**已知上游 bug**（GUI 1.0.2；源码 `desktop_app.py:3042`）

- **问题现象**：用户在 GUI 搜到《战国策校注》→ 点「开始下载/继续下载」→
  `操作没有完成`；日志两条 `UI_CALLBACK_ERROR`：
  `File "desktop_app.py", line 3042, in export — ValueError: tuple.index(x): x not in tuple`。
  「先检查第一章」则报 `FAILED TypeError probe_book() takes from 3 to 4 positional
  arguments but 5 were given`。
- **根因分析**：① `export` 回调把标注模式值拿去 `tuple.index()` 查找，当前值不在
  元组里（下拉框值与内置元组不同步，疑似"纯正文"选项漏登记或值被手动输入过）；
  ② `main()` 里 `probe_book(args.probe_book, args.probe_chapter, settings,
  want_thoughts, want_underlines)` 传 5 参，函数签名只有 4 参
  （`probe_book(book_id, chapter_uid, settings, want_underlines)`）。
- **临时方案**：下载一律走 CLI（`WeReadWorker.exe <book_id> --no-annotations`），
  成品与 GUI 同目录，GUI「本机已导出」可正常看到。**上游修复方向**：`probe_book`
  调用去掉多余实参或给函数补 `want_thoughts` 形参；`export` 回调对未知值回退默认
  而非抛异常。

---

## 五、已知问题与限制

| # | 严重度 | 问题 | 状态 |
|---|---|---|---|
| W1 | 中（性能） | note.png 类死链图片每章重复请求拖慢整书下载 ~10 倍（§四 10-15 (2)） | ✅ 本地副本已补丁；**安装版与上游仓库未修** |
| W2 | 严重（全新机不可用） | 手动登录只写 overlay 不写基础文件（上游 10-14 已修，**1.0.2 安装版仍带旧代码**） | ⚠️ 用扫码登录可绕过 |
| W3 | 中 | GUI `export` 回调 `tuple.index` 崩溃（§四 10-15 (0)） | ✅ 已修（PR #11，master `47f26dd` 前）；1.0.3 安装版未含 |
| W4 | 低 | `--probe-book` 参数个数 bug（§四 10-15 (0)） | ✅ 已修（PR #10，master）；1.0.3 安装版未含 |
| W5 | 低 | Program Files 安装位不可写，热补丁必须走工作区副本 | 已有固定流程（§0.3） |
| W6 | 外部 | 微信读书限流（HTTP 426）：双书并行首 8 分钟内出现，程序自动降并发退避；被拒章节保留断点 | 属服务端行为，冷却后续传即可 |
| W7 | 外部 | 分片 MD5 校验失败（shard mismatch）：个别章节 4 次重试后标记 INCOMPLETE 跳过 | 程序 fail-closed 拒绝出版；重跑续传补齐 |
| W8 | 外部（**已支持**） | 古文「注」角标：此前导出只有正文，注丢失 | ✅ 2026-10-20 起注角标保留并在统一附录里成条（§1.4） |
| W9 | 外部 | 旧 KOReader 备份里的 wr_skey 已过期（9-10 快照），只能用于恢复 api_key | 属会话时效，预期行为 |
| W10 | 低（测试/CI，**已修**） | Windows 下 `process_is_alive` 的 `os.kill(pid, 0)` 被当成 CTRL_C_EVENT 发给进程组，CI 测试跑到中途 `KeyboardInterrupt`（两次实测） | ✅ 已修（§四 2026-10-16 (3)）；其遗留的「取锁时间当进程创建时间」由 PR #23 再修一次 |
| W11 | 高（上传书，**已修**） | 上传书的目录把「注」和脚注编号拆成独立章节，19487 条里只有 20 章是正文；续段条目接口返回空 body | ✅ 已修：碎片过滤（§四 2026-10-18 (1)） |
| W12 | 中（承载上限） | Calibre 附录/大书承载硬上限在「XHTML 文件总数」约 110–180 个（§四 2026-10-16 研究主题） | 约束是文件数而非字节数；后汉书全本/逆流全本超限。注量级是想法约 38 倍，务实策略：附录只放想法、注保留章末 |
| W13 | 中（桌面链路缺口，**已修**） | 注角标在桌面下载器链路未转可点，且死链清正则误删全章注（汉书 26 章、后汉书 35 章） | ✅ 已修（§四 2026-10-20）；注角标现为可点「注」圆点并进附录 |
| W14 | 低（未落地） | 想法内嵌式改动点为单点、但会引入进度/书签/XPointer 偏移（§四 2026-10-16 研究主题） | 未落地；用户已拍板目标场景为电脑 EPUB 阅读器、旧书不重做、全量内嵌不截断 |
| W15 | 中（EPUB 规范） | 跨段划线被切成多段时复用同一个 `wrbadge-*` id（前 40 章实测 76 个 id 重复、多出 159 个实例），EPUBCheck 报 duplicate ID，回链只能落到第一处 | ⚠️ 未修；根因在上游划线注入，修它要改 annotations 的 id 分配 |
| W16 | 低（观感） | 无注章节仍输出一个空的 `<h2 class="wr-ap-sec">`，注分册开头连续多个只有标题的节 | 与参考书一致；未改 |
| W17 | 低（可用性） | 导航标签用「注释（续）」，而附录页标题是「注释（1/4）…（4/4）」；13 册时目录里 12 个同名「（续）」 | ⚠️ 待定：跟随参考书用（续），或改成带序号 |
| W18 | 低（文案） | 注分册导语写「正文中 [注n] 角标…点编号」，但正文角标是橙色「注」、无编号 | ⚠️ 待定：改导语文案或给角标加编号 |

---

## 六、运维与排障手册

### 6.1 标准下载流程（CLI，绕过 GUI bug）

```powershell
# 1) 搜索拿 bookId
& "C:\Users\asus\dsh-workspace\WeReadWorkerPatched\WeReadWorker.exe" --search "书名"

# 2) 确认凭证
& "C:\Users\asus\dsh-workspace\WeReadWorkerPatched\WeReadWorker.exe" --check-auth

# 3) 下载（纯正文；双书并行可开两个窗口/后台任务）
& "C:\Users\asus\dsh-workspace\WeReadWorkerPatched\WeReadWorker.exe" <bookId> --no-annotations --jobs 2

# 4) 若报 N chapter(s) remain unresolved（限流/校验失败）→ 等 60s 重跑同命令续传
```

### 6.2 暂停/恢复与强杀清理

```powershell
# 暂停：直接杀 worker（断点已逐章落盘，state.json 即时更新）
Get-Process WeReadWorker | Stop-Process -Force

# 强杀后必须清锁，否则下次启动 SystemError 失败
Get-ChildItem "$env:LOCALAPPDATA\WeReadDesktop\exports" -Recurse -Filter export.lock -Force |
  Remove-Item -Force
```

### 6.3 账号/凭证与敏感信息位置

| 文件 | 内容 | 敏感度 | 丢失后果 |
|---|---|---|---|
| `%LOCALAPPDATA%\WeReadDesktop\weread-live-settings.lua` | api_key（`wrk-…`，长期）+ 明文 cookies（可过期） | 高（api_key 等同账号授权） | 需从备份恢复或重新抄；备份源：`各类\Kindle\07_日志与诊断快照\weread-live-settings.lua` |
| `%LOCALAPPDATA%\WeReadDesktop\session-cookies.json` | 扫码会话（DPAPI 密封，仅本用户可解） | 中 | 重新扫码即可 |

**纪律**：api_key/wr_skey 一律不写进文档、聊天、日志与截图（本文件只记文件路径）。

### 6.4 日志级别与观测点

| 位置 | 内容 |
|---|---|
| `logs\desktop-general.log` | GUI 生命周期：搜索元数据、登录事件、`FAILED …` 汇总、`[STALL]` UI 卡顿栈 |
| `logs\desktop-<书籍ID>.log` | 单书：`OMISSIONS`（缺失明细 JSON）、`EPUB`（成品路径） |
| CLI stdout/stderr | `[N/M] Fetching chapter UID x`、`[RESUME] Reusing N complete`、`[RETRY]`、`[THROTTLE]`、`[UNRESOLVED]`、`[IMAGE] dropping` |

### 6.5 排障速查表

| 现象 | 根因 | 处置 |
|---|---|---|
| `Credential file not found: …weread-live-settings.lua` | 基础凭证文件被删（退出登录/清除凭证） | 从 §6.3 备份恢复 api_key 重建该文件 |
| `AUTH_OK` 但下载报 `-2010 用户不存在/未登录` | wr_skey 网页会话过期（api_key 通道正常，两回事） | GUI 扫码登录一次（点账号按钮→扫码→微信确认），看到 `session-cookies.json` 新时间戳再下 |
| worker 启动即 `FAILED SystemError <class 'OSError'>` | 强杀进程后 `export.lock` 残留 | §6.2 清锁后重跑 |
| `426 Upgrade Required` / `[THROTTLE]` | 微信读书限流（按账号） | 程序自动退避；结束时报 unresolved 则等 60s 重跑续传 |
| `Shard MD5 mismatch after 4 attempts` | 分片校验失败（服务端偶发） | 同上，重跑续传自动补 |
| `N chapter(s) remain unresolved; … EPUB publication was refused` | 缺章 fail-closed，拒绝出版 | 重跑续传至 0 unresolved，自动打包 |
| GUI 点下载报 `tuple.index(x): x not in tuple` | 问题 W3（GUI bug） | master 已修（PR #11）；1.0.3 安装版仍用 §6.1 CLI 流程 |
| GUI「先检查第一章」报 `probe_book() … 5 were given` | 问题 W4（CLI 入口 bug） | master 已修（PR #10）；1.0.3 安装版同上 |
| 每章下载 1 分钟以上 | 安装版无死链缓存（W1） | 用 `WeReadWorkerPatched` 副本跑 |
| 全新安装扫码登录后全部功能报 `Credential file not found` | QR 登录不写基础凭证 lua（v25 前的设计缺口） | 升级到 v1.0.3+；旧机器手工恢复一次或重新扫码（v25 起自动创建） |
| 登录时 UI 冻结 5–8 秒（STALL_STACK 指向 `_restrict_to_current_user`） | 杀毒软件扫描新会话文件阻塞 icacls（v25 前无超时） | 升级到 v1.0.3+（2s 超时降级）；或登录前临时停 AV 实时扫描 |
| 测试/CI 跑到一半突然 `KeyboardInterrupt`（栈顶 `weread_desktop.py` 的 `process_is_alive`） | W10：Windows 上 `os.kill(pid, 0)` = 向进程组发 Ctrl+C | 未修；重跑，或用 `-k` 排除涉及进程存活判断的用例 |
| EPUB 下载完成但有 `[MODAL] 有内容缺失` | `manifest.json` 的 omissions 明细（缺图/缺划线锚点等） | 正文 `text_complete: true` 即可放心用；细节看 manifest |

### 6.6 已知坏值与契约（改代码前先看）

- `load_credentials()`：lua 基础文件必须是 `\nreturn {` 开头的 Lua 表；api_key 与
  wr_skey 缺一不可（api_key 只认 lua 基础文件，**不认** overlay）。
- `-2010/-2012/2010` 或 errMsg 含「登录」→ 一律按会话过期处理（fetch_book 内判定）。
- 限流 status_forcelist `(408, 425, 429, 500, 502, 503, 504)` + 426 特判降并发。
- state.json 的 `want_thoughts` / `want_underlines` 与本次运行模式不一致时会重置续传（标注模式不同的断点不通用；PR #12 起缺字段的旧断点按「不兼容」处理 → 刷新暂存章节）。

### 6.7 环境重建（换电脑/重装后恢复）

```text
1) 安装 WeReadDownloader-Setup.exe（1.0.2+）
2) 恢复 %LOCALAPPDATA%\WeReadDesktop\weread-live-settings.lua
   （从 07_日志与诊断快照 备份，或网页版重抄 api_key）
3) GUI 扫码登录一次（生成新的 session-cookies.json）
4) 可选：重建补丁副本（若 dsh-workspace 丢失）：
   Copy-Item "C:\Program Files\WeReadDownloader\WeReadWorker" <目标> -Recurse
   然后按 §四 10-15 (2) 的三处改动重打（或等上游合并后免打）
```

---

## 七、附录

### 7.1 文件校验和记录（持续追加）

| 日期 | 文件 | sha256 前 8 位 | 说明 |
|---|---|---|---|
| 10-15 | `WeReadWorkerPatched\_internal\weread_desktop.py` | `9BEC87F6` | 补丁版（死链缓存 3 处改动），110,719 B |
| 10-15 | `Program Files\...\WeReadWorker\_internal\weread_desktop.py` | `C4275775` | 安装版原版（无补丁），110,348 B |
| 10-15 | `战国策校注 - full.epub` | （成品未记 sha，如需追溯见 exports\战国策校注-3300130665\manifest.json） | 0.46 MB，23/23 章 text_complete |
| 10-16 | `weread\desktop_app.py` | `D0208BBB` | v25：QR 登录写基础 lua + 登出文案，162,288 B |
| 10-16 | `weread\weread_login.py` | `D81AEB0F` | ACL 超时修复（2s），18,710 B |
| 10-16 | `installer\WeReadDesktop.iss` | `D1BBA4A5` | 版本号 1.0.3，11,650 B |
| 10-16 | `WeReadDownloader-Setup.exe`（1.0.3） | `AFCB864C` | 57,240,774 B，含 v25 + ACL 修复 |

| 10-16 | `weread/weread_desktop.py` | `7198E280` | W10 修复（OpenProcess + WaitForSingleObject），114,735 B |
| 10-16 | `weread/desktop_app.py` | `1CC7784D` | #11 标注模式 + #14 凭证卡刷新 + #16 书架失败态 + 登出走 _settings_path，166,498 B |
| 10-16 | `weread/first_run.py` | `AFEF15D0` | #15 `_settings_literal`/`_read_base_settings`，18,456 B |
| 10-16 | `weread/weread_login.py` | `D81AEB0F` | #15 就绪判定来源，18,710 B |
| 10-16 | `pytest.ini` | `F997CA96` | 新增：`python_functions = test_*`，563 B |
| 10-16 | `.github/workflows/ci.yml` | `AE1B280F` | W10 已修后的说明重写，6,779 B |
| 10-16 | `release-installer/WeReadDownloader-Setup.exe` | `DA420F0E` | 1.0.4 安装包（去组件界面+网页版），53,021,646 B |
| 10-16 | `release-fresh/WeReadWorker/WeReadWorker.exe` | `B3467379` | 1.0.4 下载组件，14,425,844 B |
| 10-16 | `release-fresh/WeReadDesktop/WeReadDesktop.exe` | `C21EC6A7` | 1.0.4 主程序，3,522,864 B |
| 10-16 | `release-fresh/WeReadWeb/WeReadWeb.exe` | `8D0C5079` | 1.0.4 网页版（**1.0.4 起不再进安装包**，仍构建供单独分发），1,339,129 B |
| 10-16 | `installer/WeReadDesktop.iss` | `175A4C4C` | 版本号 1.0.3 → 1.0.4，11,650 B |
| 10-18 | `weread/weread_desktop.py` | `5BEA6408` | 上传书碎片过滤（注/脚注/续段），121,033 B |
| 10-18 | `weread/desktop_app.py` | `25218BE6` | #21 排队下载 + 自更新，#33 stall-watch 清理，#31 焦点，178,511 B |
| 10-18 | `weread/updater.py` | `DA6A1353` | 新增：应用内自更新（GitHub Releases 薄封装），7,242 B |
| 10-18 | `weread/version.py` | `2CFC3FCA` | 新增：版本号单一事实源 1.0.4，513 B |
| 10-18 | `pytest.ini` | `3140D96F` | #29 local_corpus 标记 + #33 --capture=sys，916 B |
| 10-18 | `LICENSE` | `26E508CD` | 新增：MIT，1,088 B |
| 10-18 | `THIRD_PARTY_LICENSES.txt` | `7A643293` | 新增：第三方许可声明，3,627 B |
| 10-18 | `installer/WeReadDesktop.iss` | `D8B504F3` | #21 许可证随包分发，8,333 B |
| 10-16 | `tests/test_disk_edge_cases.py` | `E4085844` | W10 防回归断言（Windows 探针不含 os.kill），34,062 B |

> 以上 sha 对应本机工作副本（= 上游 master `47f26dd` + 两个本地文档提交）。合入用的上游提交：`47f26dd`（#13）、`8586746`（#18），#10–#12/#14–#17 在 `8586746` 之前。



| 场景 | 吞吐 | 来源 |
|---|---|---|
| 安装版串行 jobs=1（后汉书） | ~1.5 分/章（25→34 章耗时 12 分） | 22:47–23:00 实测 |
| 安装版双书并行 jobs=2（聚合） | ~6 章/分（8 分钟 49 章），期间有 426 限流 | 22:37–22:44 实测 |
| 补丁版双书并行 jobs=2（聚合） | ~11–12 章/分（3 分钟 35 章） | 23:21–23:24 实测 |

### 7.3 相关文档索引

- Kindle 主文档：`C:\Users\asus\Desktop\各类\Kindle\Kindle-技术文档与变更日志.md`
  （本项目条目在 §四 2026-10-14/15；weread.koplugin 与 importer 插件详见其 §二/§七）
- 项目 README（用户向）：`C:\Users\asus\Desktop\各类\weread-downloader\README.md`
- 上游仓库：`github.com/nianqianha/WeReadDownloader`

### 7.4 待办（未做）

- 上游 PR：死链图片缓存（W1）仍未入仓；W3/W4 已随 PR #10/#11 合入 master；W10 已随 §四 2026-10-16 (3) 修在本地工作副本（尚未推送，待用户决定）
- 后汉书剩 19 章 / 汉书剩 24 章续传并出版（断点保留中）
- 1.0.4 安装包已构建分发（SHA8 `DA420F0E`），但**不含** 2026-10-18 (0) 的排队下载/自更新/CI 修复，下一版需重新打包
- CI tests 作业已全绿：摘掉 `continue-on-error`，加进 protect-master 的 required status checks
- 安装版热补丁的提权方案（或等上游合并后重装）
