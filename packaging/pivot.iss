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
; WorkingDir is {app}, NOT the `current` link the exe is launched through.
; Windows locks a process's current directory, so a PIVOT started from these
; shortcuts would hold `versions\current` open for as long as it runs — and that
; is the one directory both this installer and the in-app updater have to unlink
; to flip to a new version. PIVOT resolves everything it needs from the exe path
; (see pivot.runtime.lifecycle.install_root), so it never needed the CWD.
Name: "{group}\{#MyAppName}"; Filename: "{app}\versions\current\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\versions\current\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Offer to launch after an interactive install; silent installs skip this
; (the in-app update mechanism relaunches PIVOT itself after applying an update).
;
; Launch the REAL version folder, not the `current` link the shortcuts use.
; Setup cannot follow that junction: it is a hardened installer process, which
; refuses to traverse a reparse point written by a non-elevated user (the
; WinError 448 behaviour noted in pivot.updates.layout.Layout.installed_versions).
; Every other process resolves it fine — Explorer launching the shortcuts, and
; PIVOT itself — but Setup's own CreateProcess through it fails with
; "CreateProcess failed; code 2", and its FileExists probe answers False, on a
; junction that is perfectly healthy. This is the version just installed and
; activated, so the concrete path launches exactly what `current` points at.
; Still gated on CurrentLinkOK: if the flip did not take, `current` is wrong and
; the shortcuts are broken, so offering a launch would be misleading.
Filename: "{app}\versions\app-{#MyAppVersion}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "Launch {#MyAppName}"; Check: CurrentLinkOK; Flags: nowait postinstall skipifsilent

[Code]
const
  // Set on junctions and symlinks; the flag that tells a link apart from the
  // real directory it stands in for.
  FILE_ATTR_REPARSE_POINT = $400;

var
  // Whether `current` ended up pointing at a version that really has the exe.
  // Gates the [Run] launch entry via CurrentLinkOK below.
  FlipSucceeded: Boolean;

function CurrentLinkOK(): Boolean;
begin
  Result := FlipSucceeded;
end;

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
  // Sequenced, not `Exec(...) and (ResultCode = 0)`: that reads ResultCode in
  // the same expression that assigns it, and Pascal Script does not promise to
  // evaluate the left operand first.
  Result := False;
  if Exec(ExpandConstant('{cmd}'),
          '/E:ON /C mklink /J "' + CurrentPath + '" "' + AppDir + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := (ResultCode = 0);
end;

function FlipTook(const CurrentPath, AppDir: String): Boolean;
begin
  // Check the link and the payload SEPARATELY. The obvious test — does
  // CurrentPath + '\<exe>' exist — is wrong here, because it makes Setup walk
  // through the junction it has just created, and Setup is precisely the
  // process that cannot: a hardened installer refuses to follow a reparse point
  // written by a non-elevated user (the same Redirection Guard behaviour noted
  // in pivot.updates.layout.Layout.installed_versions). It answers False for a
  // link that is in fact perfect and that every other process — including PIVOT
  // itself, launched through this very path — resolves without trouble.
  //
  // Neither call below traverses: FindFirst reads the reparse point itself, and
  // the exe is checked in the real version folder we linked to.
  Result := IsReparsePoint(CurrentPath) and FileExists(AppDir + '\{#MyAppExeName}');
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
  //
  // Nested rather than `Clear(...) and Make(...)`: Pascal Script does not
  // promise to evaluate operands left to right, and these two are ordered
  // operations — running Make first would fail on the existing link and let
  // Clear delete what Make had just built.
  for Attempt := 1 to 3 do
  begin
    if ClearCurrentLink(CurrentPath) then
      if MakeCurrentLink(CurrentPath, AppDir) then
        if FlipTook(CurrentPath, AppDir) then
        begin
          FlipSucceeded := True;
          Exit;
        end;
    Sleep(500);
  end;

  // Reported, not raised: an exception here surfaces as "Runtime error (at
  // 11:1368)" — which reads as a crash in Setup — and does not stop Setup
  // reaching the Finished page anyway. CurrentLinkOK suppresses the launch.
  // NB: never start a continuation line with #13/#10 — ISPP reads a leading '#'
  // as a preprocessor directive and aborts before the [Code] section compiles.
  MsgBox(
    'Setup could not point' + #13#10
    + CurrentPath + #13#10
    + 'at this version''s folder, so PIVOT has not been activated.' + #13#10 + #13#10
    + 'The usual cause is PIVOT still running: it keeps that link open, and on'
    + ' Windows a folder cannot be unlinked while a running program is using it.'
    + ' PIVOT hides in the notification area (system tray) rather than showing a'
    + ' window, so check there — quit it from the tray icon, then run this'
    + ' installer again.' + #13#10 + #13#10
    + 'Failing that, the install location must be on an NTFS drive and allow'
    + ' creating a directory junction; excluding the folder from real-time'
    + ' antivirus scanning also resolves it.',
    mbError, MB_OK);
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
