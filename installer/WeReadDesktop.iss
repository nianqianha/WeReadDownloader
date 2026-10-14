; ============================================================================
;  WeReadDownloader —— Inno Setup 6 安装脚本
;
;  设计要点：
;   * 网页版暂时封存，不再进安装包。源码（weread_web.py / webui.html /
;     packaging/WeReadWeb.spec）留在仓库里，想装回去把 [Files] 的 web 段、
;     [Icons]、[Run] 恢复并加回 web 组件即可。
;   * 现在只有一个 exe，因此只有一个安装类型和一个快捷方式，都叫
;     WeReadDownloader。以前叫「图形版（二维码登录）」，那是实现细节——界面上
;     只有一个程序，两个名字只会让人以为是两个软件。
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
#define MyAppVersion "1.0.1"
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

; 网页版暂时封存：源码（weread_web.py / webui.html / packaging/WeReadWeb.spec）
; 都留在仓库里，随时可以装回去，但不再进安装包，也不再出现在类型页/组件页/开始
; 菜单/桌面上。删掉的是分发入口，不是代码。
;
; 只剩一个安装类型：没有"选哪个"的余地，也就不会再出现"以为装了 A 其实装了 B"。
[Types]
Name: "full"; Description: "完整安装"

[Components]
Name: "graphics"; Description: "主程序（桌面窗口）"; Types: full
Name: "worker"; Description: "下载组件（必选）"; Types: full; Flags: fixed

; checkedonce 是 [Tasks] 里唯一能表达「默认勾上」的 flag（checked 不是合法值）。
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 主程序。源目录是 release-fresh —— 每次发布前重新构建到那里，而不是复用某个
; 历史 release-* 目录：之前指向 release-qrlogin，那个目录停留在几轮改动之前，
; 装出来的程序和仓库里的源码根本不是一回事。
Source: "..\release-fresh\WeReadDesktop\*"; DestDir: "{app}\WeReadDesktop"; \
    Components: graphics; Flags: ignoreversion recursesubdirs createallsubdirs

; 下载组件
Source: "..\release-fresh\WeReadWorker\*"; DestDir: "{app}\WeReadWorker"; \
    Components: worker; Flags: ignoreversion recursesubdirs createallsubdirs

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

[Run]
Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; Description: "立即启动 {#MyAppName}"; \
    WorkingDir: "{app}\WeReadDesktop"; Components: graphics; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 正常卸载已由 [Files] 处理；这里兜底清掉可能残留的空目录
Type: filesandordirs; Name: "{app}\WeReadDesktop"
Type: filesandordirs; Name: "{app}\WeReadWeb"
Type: filesandordirs; Name: "{app}\WeReadWorker"

[InstallDelete]
; 1.0.0 把程序装在 {autopf}\WeReadDesktop，1.0.1 起目录名与产品名统一为
; WeReadDownloader。留着旧目录会让人以为还装着另一个版本，所以升级时清掉它。
; 只删程序目录，不动 %LOCALAPPDATA%\WeReadDesktop（那是用户的导出与凭证）。
Type: filesandordirs; Name: "{autopf}\WeReadDesktop"
