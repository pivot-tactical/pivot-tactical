; Inno Setup script for PIVOT-Tactical (spec §3.7, §9.1).
;
; Turns the PyInstaller onedir output (dist\PIVOT-Tactical\) into a professional
; Windows installer: Start-menu shortcut, uninstaller, optional all-users install.
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
; Default: per-user install under LocalAppData\Programs — no UAC, always writable,
; self-updates work without admin rights. The user can click "Install for all users"
; in the wizard (or pass /ALLUSERS on the command line) to elevate and install
; system-wide under Program Files instead.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Default to no elevation (per-user); allow optional all-users upgrade via dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
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

[Dirs]
; Create writable data and versions directories at install time. Setting
; Permissions here means a system-wide (Program Files) install still works: the
; installer (running elevated) grants Users modify access so the app can create
; its DB/recordings and self-update without needing admin rights on every launch.
Name: "{app}\data"; Permissions: users-modify
Name: "{app}\versions"; Permissions: users-modify

[InstallDelete]
; Clean up a legacy flat install (pre-side-by-side, where the bundle lived
; directly under {app}) so its files don't linger next to the new versions\
; tree — a stray old PIVOT-Tactical.exe/_internal would otherwise sit unused
; and confusing right beside the shortcut's real target.
Type: files; Name: "{app}\{#MyAppExeName}"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Lay the PyInstaller onedir bundle down as its own versioned folder rather
; than directly in {app} — the side-by-side layout (Chrome/VS Code/Squirrel
; model, §3.7.5): each version gets a folder of its own and a `current` link
; always points at the active one. [Code] below flips that link once the files
; are all in place, exactly like the in-app updater's atomic version flip.
Source: "..\dist\PIVOT-Tactical\*"; DestDir: "{app}\versions\app-{#MyAppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\versions\current\{#MyAppExeName}"; WorkingDir: "{app}\versions\current"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\versions\current\{#MyAppExeName}"; WorkingDir: "{app}\versions\current"; Tasks: desktopicon

[Run]
; Offer to launch after an interactive install; silent installs skip this
; (the in-app update mechanism relaunches PIVOT itself after applying an update).
; Go through `current` so this always launches whichever build is active.
Filename: "{app}\versions\current\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure FlipCurrentLink();
var
  ResultCode: Integer;
  AppDir, CurrentPath: String;
begin
  CurrentPath := ExpandConstant('{app}\versions\current');
  AppDir := ExpandConstant('{app}\versions\app-{#MyAppVersion}');

  // The same atomic re-point the in-app updater performs (see
  // pivot.updates.layout.Layout.activate): a junction looks like an empty
  // directory to Windows, so an empty RemoveDir clears the reparse point
  // without touching the version it pointed at; only a real leftover
  // directory needs the recursive fallback. Then mklink /J — any user can
  // create a junction, unlike a symlink, which needs Developer Mode/admin.
  if DirExists(CurrentPath) then
  begin
    if not RemoveDir(CurrentPath) then
      DelTree(CurrentPath, True, True, True);
  end;
  Exec(ExpandConstant('{cmd}'), '/C mklink /J "' + CurrentPath + '" "' + AppDir + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    FlipCurrentLink();
end;
