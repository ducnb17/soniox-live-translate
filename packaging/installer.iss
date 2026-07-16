; Inno Setup script for Soniox Live Translate
; Build:  iscc /DAPP_VERSION=0.1.0 packaging/installer.iss
; Output: dist/SonioxLiveTranslate-Setup-0.1.0.exe

#ifndef APP_VERSION
  #define APP_VERSION "0.1.0"
#endif

#define AppName       "Soniox Live Translate"
#define AppExeName    "SonioxLiveTranslate.exe"
#define AppPublisher  "Soniox Live Translate"
#define AppURL        "https://soniox.com"

[Setup]
AppId={{8F4E2D7A-1C3B-4E5F-9D6E-7A8B9C0D1E2F}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppVerName={#AppName} {#APP_VERSION}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=SonioxLiveTranslate-Setup-{#APP_VERSION}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
LicenseFile=
InfoBeforeFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup"; Description: "Start with &Windows (system tray)"; GroupDescription: "Additional icons:; Flags: unchecked

[Files]
; The whole PyInstaller onedir goes into the install folder.
Source: "dist\SonioxLiveTranslate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{commonstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill the tray app before uninstalling its files.
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExeName} /T /F"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
