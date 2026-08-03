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
const
  // Set on junctions and symlinks; the flag that tells a link apart from the
  // real directory it stands in for.
  FILE_ATTR_REPARSE_POINT = $400;

function IsReparsePoint(const Path: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  // FindFirst on a full path returns that entry itself, and — unlike DirExists,
  // which resolves the link — still reports a junction whose target has gone
  // missing. That dangling shape is exactly what a half-finished flip leaves.
  if FindFirst(Path, FindRec) then
  begin
    Result := (FindRec.Attributes and FILE_ATTR_REPARSE_POINT) <> 0;
    FindClose(FindRec);
  end;
end;

function ClearCurrentLink(const CurrentPath: String): Boolean;
begin
  if IsReparsePoint(CurrentPath) then
    // Unlink ONLY. RemoveDir clears the reparse point without following it,
    // which is the whole point: DelTree would walk through the junction and
    // delete the contents of the version it points at. When the version being
    // installed is the one `current` already points at (a repair, or a reinstall
    // of the same prerelease), that is the bundle [Files] just laid down — the
    // flip then re-links `current` to an empty folder and the install ends with
    // "Unable to execute file ... CreateProcess failed; code 2".
    Result := RemoveDir(CurrentPath)
  else if DirExists(CurrentPath) then
    // A *real* directory under this name can only be debris from a botched
    // earlier flip — no version is ever installed here — so recursing is safe.
    Result := RemoveDir(CurrentPath) or DelTree(CurrentPath, True, True, True)
  else
    Result := True;
end;

function MakeCurrentLink(const CurrentPath, AppDir: String): Boolean;
var
  ResultCode: Integer;
begin
  // mklink /J — any user can create a junction, unlike a symlink, which needs
  // Developer Mode/admin. /E:ON forces command extensions on: mklink is an
  // extension command, so a machine where they are disabled by policy would
  // otherwise fail with "'mklink' is not recognized".
  Result := Exec(ExpandConstant('{cmd}'),
                 '/E:ON /C mklink /J "' + CurrentPath + '" "' + AppDir + '"',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure FlipCurrentLink();
var
  AppDir, CurrentPath: String;
  Attempt: Integer;
begin
  CurrentPath := ExpandConstant('{app}\versions\current');
  AppDir := ExpandConstant('{app}\versions\app-{#MyAppVersion}');

  // The same atomic re-point the in-app updater performs (see
  // pivot.updates.layout.Layout.activate). Retried, because an on-access virus
  // scan of the files that just landed can hold the old link open for a moment.
  for Attempt := 1 to 3 do
  begin
    if ClearCurrentLink(CurrentPath) and MakeCurrentLink(CurrentPath, AppDir) then
      // Only the exe being reachable *through* `current` proves the flip worked.
      // Every shortcut and the post-install launch resolve that path, so a link
      // that silently didn't take must fail here — while Setup can still say
      // why — rather than at the user's last click, or later at a dead shortcut.
      if FileExists(CurrentPath + '\{#MyAppExeName}') then
        Exit;
    Sleep(500);
  end;

  RaiseException(
    'Setup could not link' + #13#10 + CurrentPath + #13#10 + 'to this version''s folder.' +
    #13#10#13#10 +
    'PIVOT installs each version side by side and points a "current" link at the' +
    ' active one. That needs an NTFS install location and permission to create a' +
    ' directory junction. Choosing a different install folder, or excluding this' +
    ' one from real-time antivirus scanning, usually resolves it.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    FlipCurrentLink();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // `current` was created from [Code], so the uninstaller has no record of it
  // and would leave a junction behind pointing at a version it just deleted —
  // enough to confuse a later reinstall. Unlink it before the version folders go
  // (ClearCurrentLink never follows it, so this deletes nothing but the link).
  if CurUninstallStep = usUninstall then
    ClearCurrentLink(ExpandConstant('{app}\versions\current'));
end;
