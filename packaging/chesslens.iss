; Inno Setup script for ChessLens. Built by scripts\build_windows.py, which passes the defines below.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\ChessLens"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\assets\chesslens.ico"
#endif
#ifndef InfoFile
  #define InfoFile "before-install.txt"
#endif

[Setup]
AppId={{6F1D2B7E-3C55-4C8B-9E2A-5B7C0D4A91E3}
AppName=ChessLens
AppVersion={#AppVersion}
AppVerName=ChessLens {#AppVersion}
AppPublisher=ChessLens
DefaultDirName={autopf}\ChessLens
DefaultGroupName=ChessLens
DisableProgramGroupPage=yes
; Installs for the current user without admin rights; an admin can still choose all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=ChessLens-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ChessLens.exe
InfoBeforeFile={#InfoFile}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "stockfish"; Description: "Install Stockfish, the chess engine, with winget (downloads it from the internet)"; GroupDescription: "Engine:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\ChessLens"; Filename: "{app}\ChessLens.exe"
Name: "{group}\ChessLens user guide"; Filename: "{app}\_internal\docs\user-guide.md"
Name: "{group}\Uninstall ChessLens"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ChessLens"; Filename: "{app}\ChessLens.exe"; Tasks: desktopicon

[Run]
; winget shows its own window and asks for any agreements itself.
Filename: "winget"; Parameters: "install --id Stockfish.Stockfish --exact"; StatusMsg: "Installing Stockfish with winget..."; Flags: waituntilterminated; Tasks: stockfish
Filename: "{app}\ChessLens.exe"; Description: "{cm:LaunchProgram,ChessLens}"; Flags: nowait postinstall skipifsilent

[Code]
// Personal data lives in %APPDATA%\ChessLens (learned pieces, settings, opt-in game history).
// Uninstalling asks before deleting it rather than leaving it silently or removing it unasked.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\ChessLens');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete your ChessLens data (learned pieces, settings and saved game history) in ' + DataDir + '?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
