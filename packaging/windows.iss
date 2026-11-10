; Inno Setup script: wraps dist\RAG-Help-Desk into a normal Windows installer.
; Build: iscc /DAppVersion=0.1.0 packaging\windows.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0B1E52-3C0A-4F5D-9B7E-6B2D3B6E9A11}
AppName=RAG Help Desk
AppVersion={#AppVersion}
AppPublisher=rag-langgraph-lab
DefaultDirName={localappdata}\Programs\RAG Help Desk
DefaultGroupName=RAG Help Desk
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=RAG-Help-Desk-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RAG-Help-Desk.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\RAG-Help-Desk\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\RAG Help Desk"; Filename: "{app}\RAG-Help-Desk.exe"
Name: "{group}\Uninstall RAG Help Desk"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RAG Help Desk"; Filename: "{app}\RAG-Help-Desk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RAG-Help-Desk.exe"; Description: "Start RAG Help Desk"; Flags: nowait postinstall skipifsilent
