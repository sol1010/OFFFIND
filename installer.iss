; OFFFIND 설치 프로그램. build_exe.bat로 dist\OFFFIND 를 먼저 만든 뒤 이 스크립트를
; Inno Setup(ISCC.exe)으로 컴파일한다. 관리자 권한이 필요 없도록 사용자 폴더(AppData\Local)에
; 설치한다 — 어차피 자동 시작 등록도 HKCU 레지스트리라 관리자 권한 자체가 필요 없다.
#define MyAppName "OFFFIND"
#define MyAppVersion "1.1"
#define MyAppExeName "OFFFIND.exe"

[Setup]
AppId={{B4E6D9C1-2F3A-4B8E-9C7D-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=OFFFIND-Setup
SetupIconFile=icon_offfind.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 아이콘 만들기"; GroupDescription: "추가 아이콘:"

[Files]
Source: "dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; "윈도우 시작 시 자동 실행"은 설치 프로그램이 아니라 앱 자체의 옵션 창(레지스트리
; HKCU Run 키, startup.py)에서 켜고 끌 수 있다 — 여기서 또 만들면 둘 다 켰을 때
; 시작 시 두 번 실행되는 문제가 생긴다.

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
