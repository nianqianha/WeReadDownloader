; ============================================================================
;  WeReadDownloader —— Inno Setup 6 安装脚本
;
;  设计要点：
;   * 1.0.4 起只有「主程序 + 下载组件」两件，且都不给用户选。装一个工具不该被问
;     "你要装哪些组件"——这两件是一起才跑得起来的整体，问出来只会让人以为能只装
;     一半。用户明确要求去掉组件选择界面，所以 [Types]/[Components] 整段删除。
;   * 网页版 1.0.5 起已从仓库删除（代码/构建/测试一并移除）：它长期带病（脚本
;     语法错误导致整页无响应），维护成本高于价值。安装包里只保留主程序与下载组件。
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
; 1.0.4 删掉组件选择界面与网页版：见文件头设计要点。
#define MyAppVersion "1.0.5"
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
; 这个名字必须和 desktop_app.py 的 SINGLE_INSTANCE_KEY 逐字一致：改名时漏了这里，
; CloseApplications 就再也认不出正在运行的实例，安装会直接去覆盖被占用的 exe，
; 报 "拒绝访问" 而不是提示关闭。
; test_multi_account_isolation.test_installer_closes_the_running_apps 会守住这两处。
; 1.0.5 起只有图形版：Web 的互斥体名随网页版一起删了，留着没有任何东西会去创建它。
AppMutex=WeReadDownloader-Graphics
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

; 1.0.4：网页版从安装包里彻底移除，[Types]/[Components] 一起删掉。
; 用户装一个工具不该被问"你要装哪些组件"——主程序和下载组件是一起才跑得起来的
; 整体，问出来只会让人以为能只装一半。原先 1.0.2 把三个组件做成可勾选是过度设计，
; 用户明确要求去掉这个界面，所以回到"装就是一个完整可用的程序"。
; 1.0.5：网页版源码与构建脚本一并删除，release-fresh/ 里也不再有 WeReadWeb。

; 桌面图标任务：默认勾上（checkedonce 是 [Tasks] 里唯一能表达"默认勾选"的 flag，
; checked 不是合法值）。
[Tasks]
Name: "desktopicon"; Description: "创建主程序桌面快捷方式"; GroupDescription: "附加任务:"; \
    Flags: checkedonce

[Files]
; 主程序。源目录是 release-fresh —— 每次发布前重新构建到那里，而不是复用某个
; 历史 release-* 目录：之前指向 release-qrlogin，那个目录停留在几轮改动之前，
; 装出来的程序和仓库里的源码根本不是一回事。
Source: "..\release-fresh\WeReadDesktop\*"; DestDir: "{app}\WeReadDesktop"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; 下载组件
Source: "..\release-fresh\WeReadWorker\*"; DestDir: "{app}\WeReadWorker"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; 图标文件必须随程序一起安装：快捷方式的 IconLocation 只能写绝对路径，
; 写相对路径（..\assets\...）时 Windows 解析不到，桌面图标就变成空白
; （已实测：IconLocation 存成 '..\assets\weread-app.ico,0'，该路径不存在）。
Source: "..\assets\weread-app.ico"; DestDir: "{app}\assets"; \
    Flags: ignoreversion

; 许可证随安装包分发：LICENSE 为本程序自身（MIT），THIRD_PARTY_LICENSES.txt 为
; 打包进 exe 的第三方库许可声明。自更新拉起的 Setup.exe 装出去也含这两份，
; 满足 MIT/BSD/Apache 的署名义务，且不与未签名的 SmartScreen 警告产生额外风险。
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

; 快捷方式必须显式指定图标。不设 IconFilename 时会沿用 PyInstaller exe 内嵌的
; 通用图标，桌面上会变成一个很难看的默认图标（已实测踩过：IconLocation 为 ",0"）。
; 名字直接用 WeReadDownloader，不再叫「图形版（二维码登录）」——那是实现细节，
; 界面上只有一个程序，两个名字只会让人以为是两个软件。
[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; Description: "立即启动 {#MyAppName}"; \
    WorkingDir: "{app}\WeReadDesktop"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 正常卸载已由 [Files] 处理；这里兜底清掉可能残留的空目录。
; WeReadWeb 那行是历史包袱：1.0.2/1.0.3 装过网页版的机器升级到 1.0.4，安装包里
; 已经没有这个组件了，不写这行卸载时会留一个空目录，而「卸载完了还留一个文件夹」
; 会被当成没卸干净。
Type: filesandordirs; Name: "{app}\WeReadDesktop"
Type: filesandordirs; Name: "{app}\WeReadWeb"
Type: filesandordirs; Name: "{app}\WeReadWorker"

[InstallDelete]
; 1.0.0 把程序装在 {autopf}\WeReadDesktop，1.0.1 起目录名与产品名统一为
; WeReadDownloader。留着旧目录会让人以为还装着另一个版本，所以升级时清掉它。
; 只删程序目录，不动 %LOCALAPPDATA%\WeReadDesktop（那是用户的导出与凭证）。
Type: filesandordirs; Name: "{autopf}\WeReadDesktop"
