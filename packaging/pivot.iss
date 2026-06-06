; Inno Setup script for PIVOT-Tactical (spec §3.7, §9.1).
;
; Turns the PyInstaller onedir output (dist\PIVOT-Tactical\) into a professional
; Windows installer: Program Files install, Start-menu shortcut, uninstaller.
; This is the artifact WinSparkle downloads and runs to apply an update — the
; running app is closed, the install is swapped on disk, and PIVOT is relaunched,
; which is why a directly-run .exe could never update itself.
;
; Build (in CI, version comes from the tag):
;   iscc /DMyAppVersion=1.2.0 packaging\pivot.iss
; Output:
;   dist\installer\PIVOT-Tactical-Setup-v1.2.0.exe
;
; Silent apply (what WinSparkle invokes):  Setup.exe /VERYSILENT /SUPPRESSMSGBOXES
; WinSparkle relaunches PIVOT after the installer exits, so no [Run] on silent.

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
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=PIVOT-Tactical-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 64-bit only, matching the win64 build.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Close a running PIVOT before upgrading, and let WinSparkle relaunch it.
CloseApplications=yes
RestartApplications=no
AppMutex=PIVOT-Tactical-Single-Instance
; A plain LAN tool: install per-machine but don't force an admin prompt path
; the user can't satisfy — fall back to per-user if not elevated.
PrivilegesRequiredOverridesAllowed=dialog commandline

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The entire onedir bundle, including WinSparkle.dll which CI drops in beside
; the exe before this script runs.
Source: "dist\PIVOT-Tactical\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch after an interactive install; skipped on silent (WinSparkle
; relaunches PIVOT itself when applying an update silently).
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
