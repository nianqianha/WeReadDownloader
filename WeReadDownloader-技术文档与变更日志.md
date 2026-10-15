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
| 安装版本 | 1.0.3（ installer `installer\WeReadDesktop.iss`，SHA8 `AFCB864C`） | v25 修复版已构建未分发安装；桌面 `WeReadDownloader-Setup.exe` 为 1.0.3 |
| 凭证状态 | ✅ 可用（api_key + 扫码会话） | §6.3 凭证位置；wr_skey 会过期，重新扫码即可 |
| 运行入口（推荐） | ✅ `WeReadWorkerPatched\WeReadWorker.exe`（工作区副本，含死链图片缓存补丁） | §四 2026-10-15 (2)；GUI「开始下载」「先检查第一章」按钮有 bug，用 CLI 绕过 |
| GUI 可用性 | ⚠️ 搜索/书架可用；下载按钮报错 | `export` 回调 `tuple.index` 崩溃（问题 W3）+ `probe_book` 参数 bug（问题 W4） |
| 本地补丁 | ✅ `_internal\weread_desktop.py` `9BEC87F6`（原版 `C4275775`） | 副本位于 `C:\Users\asus\dsh-workspace\WeReadWorkerPatched\`；正式修复待上游（问题 W1） |
| 下载进度 | 《战国策校注》✅ 23/23 已打包；《后汉书》136/155、《汉书》147/171 已暂停 | 断点在 `%LOCALAPPDATA%\WeReadDesktop\exports\`，续传不重复下载 |
| 提速补丁效果 | 约 10 倍（1.5 分/章 → 约 5~6 章/分，双书并行 jobs=2） | §四 2026-10-15 (2) 实测数据 |

### 0.2 四条铁律（违反就会踩已知坑）

1. **改完代码必须更新本文件**——§四加条目、§0.1改看板、§7.1记 sha、新故障进 §6.5。
2. **强杀 worker 后必须清 `export.lock`**——`atexit` 清理不会执行，残留锁会让下次
   启动直接 `SystemError` 失败（§6.5 排障第一行）。
3. **凭证文件是两份契约**——`weread-live-settings.lua`（api_key 基础文件，缺失即
   "Credential file not found"）+ `session-cookies.json`（扫码 overlay，DPAPI 密封）。
   任何只写其一的登录方式都是坏的（§四 2026-10-14 上游条目）。
4. **速率限制是按账号的**——双书并行 + jobs=2 的聚合吞吐优于串行；被 426 限流时
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

| 模式 | 划线 | 想法 | 速度 | 产物 reviews.jsonl |
|---|---|---|---|---|
| `--no-annotations`（纯正文） | 无 | 无 | 最快 | 无 |
| `--no-thoughts`（只要划线） | 有 | 无 | 中 | 有（仅落点） |
| 默认（划线+想法） | 有 | 有 | 最慢 | 有（完整） |

成品目录：`exports\书名-书籍ID\`：`书名 - full.epub`、`书名 - full.weread.zip`
（KOReader 导入包）、`manifest.json`（含 omissions 缺失说明）、`reviews.jsonl`。
断点目录：同名 `.work\`（`state.json` 记 `completed/total`，`chapter-uid-*.body/css/json`
逐章缓存，`export.lock` 运行锁）。

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

**Added**（配套 Kindle 侧交付，详见 Kindle 文档同日条目）

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
| W1 | 中（性能） | note.png 类死链图片每章重复请求拖慢整书下载 ~10 倍（§四 10-15 (2)） | ✅ 本地副本已补丁；**安装版与上游未修** |
| W2 | 严重（全新机不可用） | 手动登录只写 overlay 不写基础文件（上游 10-14 已修，**1.0.2 安装版仍带旧代码**） | ⚠️ 用扫码登录可绕过 |
| W3 | 中 | GUI `export` 回调 `tuple.index` 崩溃（§四 10-15 (0)） | ⚠️ CLI 绕过 |
| W4 | 低 | `--probe-book` 参数个数 bug（§四 10-15 (0)） | ⚠️ 未修，不影响下载路径 |
| W5 | 低 | Program Files 安装位不可写，热补丁必须走工作区副本 | 已有固定流程（§0.3） |
| W6 | 外部 | 微信读书限流（HTTP 426）：双书并行首 8 分钟内出现，程序自动降并发退避；被拒章节保留断点 | 属服务端行为，冷却后续传即可 |
| W7 | 外部 | 分片 MD5 校验失败（shard mismatch）：个别章节 4 次重试后标记 INCOMPLETE 跳过 | 程序 fail-closed 拒绝出版；重跑续传补齐 |
| W8 | 外部 | 古文「注」角标不支持（README 已知限制；后汉书类书籍只有正文） | 上游设计如此 |
| W9 | 外部 | 旧 KOReader 备份里的 wr_skey 已过期（9-10 快照），只能用于恢复 api_key | 属会话时效，预期行为 |

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
| `logs\desktop-<书籍ID>.log` | 单书：`OMISSIONS`（缺失明细 JSON）、`EPUB/IMPORT_BUNDLE/DONE`（出版路径） |
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
| GUI 点下载报 `tuple.index(x): x not in tuple` | 问题 W3（GUI bug） | 用 §6.1 CLI 流程 |
| GUI「先检查第一章」报 `probe_book() … 5 were given` | 问题 W4（CLI 入口 bug） | 同上 |
| 每章下载 1 分钟以上 | 安装版无死链缓存（W1） | 用 `WeReadWorkerPatched` 副本跑 |
| 全新安装扫码登录后全部功能报 `Credential file not found` | QR 登录不写基础凭证 lua（v25 前的设计缺口） | 升级到 v1.0.3+；旧机器手工恢复一次或重新扫码（v25 起自动创建） |
| 登录时 UI 冻结 5–8 秒（STALL_STACK 指向 `_restrict_to_current_user`） | 杀毒软件扫描新会话文件阻塞 icacls（v25 前无超时） | 升级到 v1.0.3+（2s 超时降级）；或登录前临时停 AV 实时扫描 |
| EPUB 下载完成但有 `[MODAL] 有内容缺失` | `manifest.json` 的 omissions 明细（缺图/缺划线锚点等） | 正文 `text_complete: true` 即可放心用；细节看 manifest |

### 6.6 已知坏值与契约（改代码前先看）

- `load_credentials()`：lua 基础文件必须是 `\nreturn {` 开头的 Lua 表；api_key 与
  wr_skey 缺一不可（api_key 只认 lua 基础文件，**不认** overlay）。
- `-2010/-2012/2010` 或 errMsg 含「登录」→ 一律按会话过期处理（fetch_book 内判定）。
- 限流 status_forcelist `(408, 425, 429, 500, 502, 503, 504)` + 426 特判降并发。
- state.json 的 `want_thoughts` 与本次运行模式不一致时会重置续传（两模式断点不通用）。

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

### 7.2 实测数据（吞吐对照）

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

- 上游 PR：死链图片缓存（W1）、probe_book 参数（W4）、export 回调容错（W3）
- 后汉书剩 19 章 / 汉书剩 24 章续传并出版（断点保留中）
- 安装版热补丁的提权方案（或等上游合并后重装）
