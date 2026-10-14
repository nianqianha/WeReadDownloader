; ============================================================================
;  WeReadDownloader —— Inno Setup 6 安装脚本
;
;  设计要点：
;   * 网页版（weread_web.py / webui.html / packaging/WeReadWeb.spec）重新进入
;     安装包。它和主程序的分界是「启动速度」而不是功能：包里不带 PIL/tkinter/Tcl
;     的 stdlib http.server 启动约 0.6 秒，带图形栈的要好几秒，所以有人只想装它。
;   * 两个安装类型（完整 / 自定义）+ 三个可自由勾选的组件。三个组件彼此有真实
;     取舍，只给一个类型等于替用户做决定。
;   * worker 默认勾上但允许取消：它被桌面窗口和网页版共用，取消界面试得出后果，
;     所以不设 fixed（fixed 会把这选择权完全锁死，用户连看都看不到）。
;   * 安装后的目录结构让 exe 找到 worker。worker_command() 的查找顺序里第二条
;     是「exe 同级目录/../WeReadWorker/」，所以 worker 放在 exe 的父级：
;         {app}\WeReadDesktop\WeReadDesktop.exe   ->  {app}\WeReadWorker\
;   * 桌面快捷方式默认勾选（[Tasks] 的 checkedonce）。
;   * 安装完成页提供「立即启动」勾选框（[Run] 的 postinstall）。
;
;  构建（产物只落在项目内，不写桌面）：
;     "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\WeReadDesktop.iss
; ============================================================================

#define MyAppName "WeReadDownloader"
; 1.0.1 修掉一个发布级缺陷：1.0.0 的首运行向导是空壳（函数体只剩 pass），
; 而且因为 weread/ 不是包、相对导入被 except 吞掉，first_run 模块根本没被打进
; 包。结果是全新安装后程序只报 "Credential file not found" 而没有出路。
; 1.0.2 把网页版加回安装包（1.0.1 阶段它是封存状态，没有分发入口）：
;   * [Types] 从单一「完整安装」拆成「完整安装 / 自定义」两个可选类型；
;   * [Components] 三个组件都可勾选：主程序（桌面窗口）、下载组件、网页版；
;   * [Files]/[Icons]/[Tasks] 增加 web 段，网页版有独立的开始菜单项、桌面
;     快捷方式和「立即启动网页版」勾选项；
;   * [UninstallDelete] 补上 {app}\WeReadWeb，卸载时把这个目录一起清掉。
#define MyAppVersion "1.0.2"
#define MyAppPublisher "Kindle 桌面端项目"

[Setup]
AppId={{7C4B2E51-9A3D-4C7B-8E15-6F2A9D4B7C30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}

; 安装位置：默认 Program Files（写入需要管理员权限）。允许命令行用
; /CURRENTUSER 降级为「仅为我安装」，此时 {autopf} 解析到
; %LOCALAPPDATA%\Programs，非管理员机器/无提权环境下也能装上并测试。
; 目录名与 AppName 保持一致：之前叫 WeReadDesktop，而产品名已是
; WeReadDownloader，开始菜单里两个名字对不上，用户会以为是两个软件。
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 产物输出到项目内 release-installer\
OutputDir=..\release-installer
; 跟 AppName 一致。README 里写的是这个名字，改这里之前两边对不上，下载链接
; 指的是一个不存在的文件。
OutputBaseFilename=WeReadDownloader-Setup

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\weread-project.ico
; 声明本安装包会创建的互斥体，安装/卸载时据此识别正在运行的实例并提示关闭，
; 与程序内的单实例守卫（weread_core.claim_single_instance）用同一组名字。
; 这两个名字必须和 desktop_app.py 的 SINGLE_INSTANCE_KEY、weread_web.py 的
; is_first_instance(...) 逐字一致：改名时漏了这里，CloseApplications 就再也认不出
; 正在运行的实例，安装会直接去覆盖被占用的 exe，报 "拒绝访问" 而不是提示关闭。
; test_multi_account_isolation.test_installer_closes_the_running_apps 会守住这三处。
AppMutex=WeReadDownloader-Graphics,WeReadDownloader-Web
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

; 网页版重新进安装包。原来的「只有一个类型」省掉了选择页，但用户装的是三个
; 有真实取舍的部件，替他做决定不如让他勾；所以恢复「完整 / 自定义」两个类型，
; 完整安装三个组件全装，自定义允许在组件页里逐项取舍。
[Types]
Name: "full"; Description: "完整安装"
Name: "custom"; Description: "自定义安装（自选组件）"; Flags: iscustom

[Components]
; 桌面窗口：带图形界面的主程序，扫码/密码登录、书架、导出都在这里。
; 界面里只有一个程序，所以名字只叫主程序，不叫「图形版（二维码登录）」——
; 那是实现细节，界面上多一个名字只会让人以为是两个软件。
Name: "graphics"; Description: "主程序（桌面窗口）"; Types: full custom

; 下载引擎。默认勾上但允许取消（没有 fixed）：网页版和主程序都要靠它真正去
; 下载，界面试不出「没装它跑不起来」的原因，所以保持默认勾选，把选择权留给
; 用户；只勾主程序而不勾它，装出来的是一个能开但不能下载的组合。
Name: "worker"; Description: "下载组件（主程序和网页版共用，建议保留）"; Types: full custom

; 网页版：浏览器界面，启动约 0.6 秒，适合只想要「点进去下载」的场景。它同样
; 依赖下载组件，只装它也一样能跑（自己会拉起 worker 进程）。
Name: "web"; Description: "网页版（浏览器界面，启动快）"; Types: full custom

; checkedonce 是 [Tasks] 里唯一能表达「默认勾上」的 flag（checked 不是合法值）。
; 桌面图标任务按组件拆成两条：主程序和网页版是两个独立程序，各自的快捷方式
; 能否创建要能单独决定，共用一条会变成「装一个就同时要另一个的桌面图标」。
[Tasks]
Name: "desktopicon"; Description: "创建主程序桌面快捷方式"; GroupDescription: "附加任务:"; \
    Components: graphics; Flags: checkedonce
Name: "webicon"; Description: "创建网页版桌面快捷方式"; GroupDescription: "附加任务:"; \
    Components: web; Flags: unchecked

[Files]
; 主程序。源目录是 release-fresh —— 每次发布前重新构建到那里，而不是复用某个
; 历史 release-* 目录：之前指向 release-qrlogin，那个目录停留在几轮改动之前，
; 装出来的程序和仓库里的源码根本不是一回事。
Source: "..\release-fresh\WeReadDesktop\*"; DestDir: "{app}\WeReadDesktop"; \
    Components: graphics; Flags: ignoreversion recursesubdirs createallsubdirs

; 下载组件
Source: "..\release-fresh\WeReadWorker\*"; DestDir: "{app}\WeReadWorker"; \
    Components: worker; Flags: ignoreversion recursesubdirs createallsubdirs

; 网页版（浏览器界面）。纯 stdlib 的 http.server 包，不带 PIL/tkinter/Tcl，
; 所以它单独一个目录而不是和主程序混在一起：混在一起会让主程序的图形栈把它
; 拖进杀毒深度扫描，启动从零点几秒变成好几秒，而这个组件存在的理由就是快。
; 产物已就位（WeReadWeb.exe sha8=6fb9f8a5 + _internal/webui.html），缺目录时
; ISCC 直接编译失败，不会悄悄打出一个没有网页版的包——这正是想要的行为。
Source: "..\release-fresh\WeReadWeb\*"; DestDir: "{app}\WeReadWeb"; \
    Components: web; Flags: ignoreversion recursesubdirs createallsubdirs

; 网页版图标必须随程序一起安装：快捷方式的 IconLocation 只能写绝对路径，
; 写相对路径（..\assets\...）时 Windows 解析不到，桌面图标就变成空白。
Source: "..\assets\weread-web.ico"; DestDir: "{app}\assets"; \
    Components: web; Flags: ignoreversion

; 图标文件必须随程序一起安装：快捷方式的 IconLocation 只能写绝对路径，
; 写相对路径（..\assets\...）时 Windows 解析不到，桌面图标就变成空白
; （已实测：IconLocation 存成 '..\assets\weread-app.ico,0'，该路径不存在）。
Source: "..\assets\weread-app.ico"; DestDir: "{app}\assets"; \
    Components: graphics; Flags: ignoreversion

; 快捷方式必须显式指定图标。不设 IconFilename 时会沿用 PyInstaller exe 内嵌的
; 通用图标，桌面上会变成一个很难看的默认图标（已实测踩过：IconLocation 为 ",0"）。
; 名字直接用 WeReadDownloader，不再叫「图形版（二维码登录）」——那是实现细节，
; 界面上只有一个程序，两个名字只会让人以为是两个软件。
[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"; Components: graphics
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"; \
    Tasks: desktopicon; Components: graphics

; 网页版的菜单项带「0.6s 启动」而不是「网页版（浏览器界面）」：组件页已经说清
; 它是浏览器界面，菜单里重复一遍没有信息量，快速启动才是它和主程序的区别。
Name: "{group}\网页版（0.6s 启动）"; Filename: "{app}\WeReadWeb\WeReadWeb.exe"; \
    WorkingDir: "{app}\WeReadWeb"; IconFilename: "{app}\assets\weread-web.ico"; Components: web

Name: "{autodesktop}\网页版（0.6s 启动）"; Filename: "{app}\WeReadWeb\WeReadWeb.exe"; \
    WorkingDir: "{app}\WeReadWeb"; IconFilename: "{app}\assets\weread-web.ico"; \
    Tasks: webicon; Components: web

[Run]
Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; Description: "立即启动 {#MyAppName}"; \
    WorkingDir: "{app}\WeReadDesktop"; Components: graphics; \
    Flags: nowait postinstall skipifsilent

Filename: "{app}\WeReadWeb\WeReadWeb.exe"; Description: "立即启动网页版"; \
    WorkingDir: "{app}\WeReadWeb"; Components: web; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 正常卸载已由 [Files] 处理；这里兜底清掉可能残留的空目录。
; 三个组件各一行：自定义安装里用户可能没装某一个，缺的那行会在卸载时留下
; 空目录，而「卸载完了还留一个文件夹」会被当成没卸干净。
; {app}\WeReadWeb 是 1.0.2 新增的，1.0.1 没有这行，所以旧版升级到新版再卸载
; 会残留网页版目录。
Type: filesandordirs; Name: "{app}\WeReadDesktop"
Type: filesandordirs; Name: "{app}\WeReadWeb"
Type: filesandordirs; Name: "{app}\WeReadWorker"

[InstallDelete]
; 1.0.0 把程序装在 {autopf}\WeReadDesktop，1.0.1 起目录名与产品名统一为
; WeReadDownloader。留着旧目录会让人以为还装着另一个版本，所以升级时清掉它。
; 只删程序目录，不动 %LOCALAPPDATA%\WeReadDesktop（那是用户的导出与凭证）。
Type: filesandordirs; Name: "{autopf}\WeReadDesktop"
