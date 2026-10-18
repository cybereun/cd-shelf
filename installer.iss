; Inno Setup script. APP_VERSION 환경변수로 버전을 받는다.
#define MyAppName "CD Shelf"
#define MyAppExe "CDShelf.exe"
#define MyAppVersion GetEnv("APP_VERSION")
#if MyAppVersion == ""
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{B6F1D0A2-5C7E-4C0B-9E55-0C4D5A1F7A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=CD Shelf
DefaultDirName={localappdata}\Programs\CDShelf
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=CDShelf-Setup
SetupIconFile=assets\cdshelf.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\CDShelf\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; 일반 설치: 마지막 화면의 "실행" 체크박스
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; 자동 업데이트(조용한 설치): 설치 후 앱을 바로 다시 실행
Filename: "{app}\{#MyAppExe}"; Flags: nowait skipifnotsilent
