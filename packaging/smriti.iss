; Smriti — per-user Windows installer (Inno Setup 6).
; Mirrors the Obsidian context-menu installer conventions, but per-user:
; no admin, installs to %LOCALAPPDATA%\Programs\Smriti, never touches PATH,
; Hermes, ~/.claude or ~/.ssh. Build: packaging/build.sh
;   ISCC /DMyAppVersion=0.5.0 packaging\smriti.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.6.0"
#endif
#define MyAppName "Smriti"
#define MyAppPublisher "Naivedya Sahu"
#define MyAppExe "SmritiTray.exe"
#define SrcDir "..\dist\Smriti"

[Setup]
AppId={{B3E7A1C2-5D4F-4A8B-9C6E-2F1D7E3A9B54}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Smriti
DefaultGroupName=Smriti
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Smriti-Setup-{#MyAppVersion}
SetupIconFile=assets\smriti.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; refuse to overwrite a running tray (it holds files open); Quit it first
AppMutex=SmritiTray
CloseApplications=no
ChangesEnvironment=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start Smriti tray when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "smriti-console.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu entries (Windows Search finds "Smriti")
Name: "{group}\Smriti"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"; Comment: "Smriti tray"
Name: "{group}\Smriti Console"; Filename: "{cmd}"; Parameters: "/k ""{app}\smriti-console.cmd"""; WorkingDir: "{app}"; IconFilename: "{app}\smriti.exe"; Comment: "smriti.exe switches: doctor, deps, link-test, pair, auth ..."
Name: "{group}\Uninstall Smriti"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Smriti"; Filename: "{app}\{#MyAppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Smriti"; ValueData: """{app}\{#MyAppExe}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Start Smriti (system tray)"; Flags: postinstall nowait skipifsilent
Filename: "{cmd}"; Parameters: "/k ""{app}\smriti-console.cmd"" first-run"; WorkingDir: "{app}"; Description: "Open Smriti Console for first-time setup (pair tablet, sign in)"; Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
    { the tray's own "Start with Windows" toggle writes this too }
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Smriti');
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Smriti');
    if DirExists(DataDir) and (not UninstallSilent) then
      if MsgBox('Also remove your Smriti data (credentials, SSH key, settings, logs)?' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                'Choose No to keep it for a later reinstall.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
