; Inno Setup script for PIVOT-Tactical (spec §3.7, §9.1).
;
; Turns the PyInstaller onedir output (dist\PIVOT-Tactical\) into a professional
; Windows installer: per-user install (no UAC), Start-menu shortcut, uninstaller.
; This is the first-install path; afterwards PIVOT updates itself in place via the
; verified, channel-aware staged path (download -> verify -> swap on restart).
;
; Build (in CI, version comes from the tag):
;   iscc /DMyAppVersion=1.2.0 packaging\pivot.iss
; Output (version-agnostic name for a stable download URL; the version is
; recorded in AppVersion and the release notes, not the filename):
;   dist\installer\PIVOT-Tactical-Setup.exe

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "PIVOT-Tactical"
#define MyAppPublisher "PIVOT Tactical Contributors"
#define MyAppExeName "PIVOT-Tactical.exe"
; Stable upgrade identity — keep this GUID constant across all future releases
; so each installer upgrades in place instead of installing side by side.
; ({{ ... }} is Inno's escape for a literal leading brace.)
#define MyAppId "{{8B5F2E94-1C3A-4D77-9E6B-0A1B2C3D4E5F}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install under LocalAppData\Programs — no UAC dialog, always writable.
; The app and its data (data\, versions\) all live here so self-updates work
; without administrator rights.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; No privilege escalation needed: the app and its data are entirely user-owned.
PrivilegesRequired=lowest
; Paths are relative to THIS script's directory (packaging\), so reach up to the
; repo-root dist\ that PyInstaller writes — `iscc packaging\pivot.iss` from the
; repo root then finds the bundle and emits the installer to repo-root dist\.
OutputDir=..\dist\installer
OutputBaseFilename=PIVOT-Tactical-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 64-bit only, matching the win64 build.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Close a running PIVOT before installing over it.
CloseApplications=yes
RestartApplications=no
AppMutex=PIVOT-Tactical-Single-Instance

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The entire PyInstaller onedir bundle (repo-root dist\, one level up from here).
Source: "..\dist\PIVOT-Tactical\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Offer to launch after an interactive install; silent installs skip this
; (the in-app update mechanism relaunches PIVOT itself after applying an update).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
