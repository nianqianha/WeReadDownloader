; ============================================================================
;  微信读书电脑端下载器 —— Inno Setup 6 安装脚本
;
;  设计要点：
;   * 安装后的目录结构让两个版本共用同一份下载组件。worker_command() 的
;     查找顺序里第二条是「exe 同级目录/../WeReadWorker/」，因此把两个 exe
;     分别放在 WeReadDesktop\ 与 WeReadWeb\，worker 放在它们共同的父级：
;         {app}\WeReadDesktop\WeReadDesktop.exe   ->  {app}\WeReadWorker\
;         {app}\WeReadWeb\WeReadDesktop.exe       ->  {app}\WeReadWorker\
;     这样只装一份 133 MB 的 worker，而不是两份 266 MB。
;   * 版本由用户在安装时勾选（[Components]），worker 是 fixed 组件，
;     选中任一版本都会自动带上。
;   * 桌面快捷方式默认勾选（[Tasks] 的 checkedonce）。
;   * 安装完成页提供「立即启动」勾选框（[Run] 的 postinstall）。
;
;  构建（产物只落在项目内，不写桌面）：
;     "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\WeReadDesktop.iss
; ============================================================================

#define MyAppName "微信读书电脑端下载器"
#define MyAppVersion "1.0.0"
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
DefaultDirName={autopf}\WeReadDesktop
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 产物输出到项目内 release-installer\
OutputDir=..\release-installer
OutputBaseFilename=WeReadDesktop-Setup

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\weread-project.ico
; 声明本安装包会创建的互斥体，安装/卸载时据此识别正在运行的实例并提示关闭，
; 与程序内的单实例守卫（weread_core.claim_single_instance）用同一组名字。
AppMutex=WeReadDesktop-Graphics,WeReadDesktop-Web
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

; 注意：[Types] 的名字故意不叫 graphics/web —— 它们若与 [Components] 的组件
; 同名，Inno Setup 在解析 /COMPONENTS 过滤与组件选择时会歧义，导致未选中的
; 组件也被安装（已实测踩过：只选 graphics 仍装上了 web 的 56 个文件）。
[Types]
Name: "custom"; Description: "自定义选择要安装的版本"
Name: "full"; Description: "完整安装（图形版 + 网页版）"
Name: "onlygraphics"; Description: "仅图形版（二维码登录）"
Name: "onlyweb"; Description: "仅网页版（0.6s 启动）"

; 用「安装类型」页而不是「组件勾选」页。原因是 [Components] 没有"默认勾选"这个
; flag（只支持 fixed/exclusive/dontinheritcheckboxes 等），custom 类型下实测三个
; 组件全是未勾选（BM_GETCHECK 读清单框确认），装出来是空壳。类型页则完全确定：
; 默认 full 装齐两版，用户想只要一个就选 onlygraphics / onlyweb。
[Types]
Name: "full"; Description: "完整安装（图形版 + 网页版）"
Name: "onlygraphics"; Description: "仅图形版（二维码登录）"
Name: "onlyweb"; Description: "仅网页版（0.6s 启动）"

[Components]
Name: "graphics"; Description: "图形版（二维码登录，桌面窗口程序）"; Types: full onlygraphics
Name: "web"; Description: "网页版（0.6s 启动，本地服务 + 浏览器）"; Types: full onlyweb
Name: "worker"; Description: "下载组件（两个版本共用，必选）"; Types: full onlygraphics onlyweb; Flags: fixed

; checkedonce 是 [Tasks] 里唯一能表达「默认勾上」的 flag（checked 不是合法值）。
; 至于它在清单框里是否真显示为勾选，由 installer-drive.py 的干净跑给出地面真相。
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 图形版（二维码登录）
Source: "..\release-qrlogin\WeReadDesktop\*"; DestDir: "{app}\WeReadDesktop"; \
    Components: graphics; Flags: ignoreversion recursesubdirs createallsubdirs

; 网页版（0.6s 启动）
Source: "..\release-web\WeReadDesktop\*"; DestDir: "{app}\WeReadWeb"; \
    Components: web; Flags: ignoreversion recursesubdirs createallsubdirs

; 下载组件（两版共用，只装一份）
Source: "..\release-qrlogin\WeReadWorker\*"; DestDir: "{app}\WeReadWorker"; \
    Components: worker; Flags: ignoreversion recursesubdirs createallsubdirs

; 图标文件必须随程序一起安装：快捷方式的 IconLocation 只能写绝对路径，
; 写相对路径（..\assets\...）时 Windows 解析不到，桌面图标就变成空白
; （已实测：IconLocation 存成 '..\assets\weread-app.ico,0'，该路径不存在）。
Source: "..\assets\weread-app.ico"; DestDir: "{app}\assets"; \
    Components: graphics; Flags: ignoreversion
Source: "..\assets\weread-web.ico"; DestDir: "{app}\assets"; \
    Components: web; Flags: ignoreversion

; 快捷方式必须显式指定图标。不设 IconFilename 时会沿用 PyInstaller exe 内嵌的
; 通用图标，桌面上会变成两个很难看的默认图标（已实测踩过：IconLocation 为 ",0"）。
; 图形版用 weread-app.ico，网页版用 weread-web.ico，与桌面上原有的两个快捷方式一致。
[Icons]
Name: "{group}\图形版（二维码登录）"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"; Components: graphics
Name: "{group}\网页版（0.6s 启动）"; Filename: "{app}\WeReadWeb\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadWeb"; IconFilename: "{app}\assets\weread-web.ico"; Components: web
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\图形版（二维码登录）"; Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadDesktop"; IconFilename: "{app}\assets\weread-app.ico"; \
    Tasks: desktopicon; Components: graphics
Name: "{autodesktop}\网页版（0.6s 启动）"; Filename: "{app}\WeReadWeb\WeReadDesktop.exe"; \
    WorkingDir: "{app}\WeReadWeb"; IconFilename: "{app}\assets\weread-web.ico"; \
    Tasks: desktopicon; Components: web

[Run]
Filename: "{app}\WeReadDesktop\WeReadDesktop.exe"; Description: "立即启动图形版"; \
    WorkingDir: "{app}\WeReadDesktop"; Components: graphics; \
    Flags: nowait postinstall skipifsilent
Filename: "{app}\WeReadWeb\WeReadDesktop.exe"; Description: "立即启动网页版"; \
    WorkingDir: "{app}\WeReadWeb"; Components: web; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 正常卸载已由 [Files] 处理；这里兜底清掉可能残留的空目录
Type: filesandordirs; Name: "{app}\WeReadDesktop"
Type: filesandordirs; Name: "{app}\WeReadWeb"
Type: filesandordirs; Name: "{app}\WeReadWorker"
