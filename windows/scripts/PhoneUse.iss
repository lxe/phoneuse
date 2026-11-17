; Phone Use for Windows: the per-user installer (Inno Setup 6). Built by package.ps1, which stages the
; files and passes AppVersion, Stage, OutDir and OutName:
;   iscc /DAppVersion=0.1.0.1 /DStage=...\PhoneUse /DOutDir=... /DOutName=... PhoneUse.iss
; Installs to %LOCALAPPDATA%\Programs\Phone Use without admin. Admin is asked for once, and only for
; the optional firewall rule UxPlay needs (as windows\scripts\install.ps1 does).

#ifndef AppVersion
  #define AppVersion "0.0.0.0"
#endif
#ifndef Stage
  #error Stage (the staged PhoneUse folder) is required: run package.ps1
#endif
#ifndef OutDir
  #define OutDir "."
#endif
#ifndef OutName
  #define OutName "PhoneUse-setup"
#endif

[Setup]
AppId={{2CE2F132-B8CF-4D8D-A986-0847606B6627}
AppName=Phone Use
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=phoneuse
AppPublisherURL=https://github.com/lxe/phoneuse
AppSupportURL=https://github.com/lxe/phoneuse/issues
DefaultDirName={autopf}\Phone Use
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#OutDir}
OutputBaseFilename={#OutName}
SetupIconFile={#Stage}\Assets\AppIcon.ico
UninstallDisplayIcon={app}\PhoneUseMirror.exe
UninstallDisplayName=Phone Use
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ChangesEnvironment=yes

[Tasks]
Name: "systempython"; Description: "Use the Python already on this PC if it's compatible (3.11-3.14 from python.org; phoneuse's packages download from PyPI, and the bundled Python is installed if anything fails)"
Name: "firewall"; Description: "Let the iPhone reach Phone Use through Windows Firewall (asks for admin once)"
Name: "path"; Description: "Add the phoneuse command to PATH (for terminals and AI agents)"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start Phone Use with Windows (in the tray)"; Flags: unchecked

[Files]
Source: "{#Stage}\*"; Excludes: "\core\python"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\core\python\*"; DestDir: "{app}\core\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: NeedBundledPython
; For the system-Python check, run before the files are installed
Source: "common.ps1"; Flags: dontcopy
Source: "system-python.ps1"; Flags: dontcopy
Source: "{#Stage}\core\requirements-lock.txt"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Phone Use"; Filename: "{app}\PhoneUseMirror.exe"
Name: "{autodesktop}\Phone Use"; Filename: "{app}\PhoneUseMirror.exe"; Tasks: desktopicon

[Registry]
; The same entry the window's own "Start with Windows" setting makes (Startup.cs)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Phone Use"; ValueData: """{app}\PhoneUseMirror.exe"" --tray"; Tasks: startup

[Run]
; Not again on an update from the app (/UPDATE): the rule is for the same uxplay.exe, and the admin prompt
; would interrupt it. The task stays selected, so uninstalling still removes the rule.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\core\windows\scripts\firewall.ps1"" -Program ""{app}\core\uxplay\bin\uxplay.exe"""; Verb: runas; Flags: shellexec runhidden waituntilterminated; StatusMsg: "Adding the firewall rule for AirPlay..."; Tasks: firewall; Check: not IsAppUpdate
Filename: "{app}\PhoneUseMirror.exe"; Description: "Start Phone Use"; Flags: nowait postinstall skipifsilent
; The app's own updater (Updater.cs) runs this silently with /UPDATE=1, or /UPDATE=tray when the window was hidden
Filename: "{app}\PhoneUseMirror.exe"; Parameters: "{code:RelaunchArgs}"; Flags: nowait; Check: IsAppUpdate

[UninstallRun]
; Stop the Bluetooth HID service the way `phoneuse down` does (it outlives the rest on purpose), then the
; window and the phoneuse it started (python.exe and uxplay.exe from this folder)
Filename: "{app}\core\bin\phoneuse.cmd"; Parameters: "down"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "PhoneUseDown"
Filename: "powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden waituntilterminated; RunOnceId: "StopPhoneUse"
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\core\windows\scripts\firewall.ps1"" -Program ""{app}\core\uxplay\bin\uxplay.exe"" -Remove"; Verb: runas; Flags: shellexec runhidden waituntilterminated; RunOnceId: "RemoveFirewall"; Tasks: firewall

[UninstallDelete]
; What phoneuse makes at run time: bytecode, core\.venv (system Python), core\.venv-vision (the
; screen-reading OCR, up to 2 GB). Settings in %APPDATA%\phoneuse stay.
Type: filesandordirs; Name: "{app}\core"
Type: dirifempty; Name: "{app}"

[Code]
const
  EnvKey = 'Environment';

var
  SystemPythonOK: Boolean;

function NeedBundledPython: Boolean;
begin
  Result := not SystemPythonOK;
end;

{ Started by the app's updater: setup.exe /SILENT ... /UPDATE=1 (or /UPDATE=tray) }
function IsAppUpdate: Boolean;
begin
  Result := ExpandConstant('{param:update|}') <> '';
end;

function RelaunchArgs(Param: string): string;
begin
  if CompareText(ExpandConstant('{param:update|}'), 'tray') = 0 then Result := '--tray'
  else Result := '';
end;

procedure EnvAddPath(Dir: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then Paths := '';
  if Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0 then exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure EnvRemovePath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';');
  if P = 0 then exit;
  { Dir starts at Paths[P]: remove it with the ';' before it, or after it when it's first }
  if P > 1 then Delete(Paths, P - 1, Length(Dir) + 1)
  else Delete(Paths, P, Length(Dir) + 1);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Args: string;
begin
  if CurStep = ssInstall then
  begin
    SystemPythonOK := False;
    if WizardIsTaskSelected('systempython') then
    begin
      WizardForm.StatusLabel.Caption := 'Setting up phoneuse with the Python on this PC...';
      ExtractTemporaryFile('common.ps1');
      ExtractTemporaryFile('system-python.ps1');
      ExtractTemporaryFile('requirements-lock.txt');
      ForceDirectories(ExpandConstant('{app}\core'));
      Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\system-python.ps1') +
              '" -Core "' + ExpandConstant('{app}\core') + '" -Lock "' + ExpandConstant('{tmp}\requirements-lock.txt') + '"';
      if Exec('powershell.exe', Args, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
        SystemPythonOK := True
      else
        Log('Not using the system Python (exit ' + IntToStr(Code) + '): see %TEMP%\phoneuse-system-python.log');
    end;
    { An earlier install's other Python would otherwise stay: Backend.cs prefers core\.venv }
    if SystemPythonOK then
      DelTree(ExpandConstant('{app}\core\python'), True, True, True)
    else
      DelTree(ExpandConstant('{app}\core\.venv'), True, True, True);
  end;
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('path') then
    EnvAddPath(ExpandConstant('{app}\core\bin'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    EnvRemovePath(ExpandConstant('{app}\core\bin'));
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Phone Use');
  end;
end;
