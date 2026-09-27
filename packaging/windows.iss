; Bộ cài Windows (Inno Setup 6). Chạy sau khi đã build: pyinstaller packaging/dichyk.spec
; iscc /DAppVersion=0.1.0 packaging\windows.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6C1F4E0A-8B7D-4B7A-9D3E-3F0C2A9B51D7}
AppName=Dịch Tài Liệu Y Khoa
AppVersion={#AppVersion}
AppPublisher=Phelim
DefaultDirName={autopf}\DichYKhoa
DefaultGroupName=Dịch Y Khoa
DisableProgramGroupPage=yes
; Cài cho người dùng hiện tại, không cần quyền Administrator
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=DichYKhoa-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\DichYKhoa.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài màn hình (Desktop)"; GroupDescription: "Biểu tượng:"

[Files]
Source: "..\dist\DichYKhoa\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Dịch Y Khoa"; Filename: "{app}\DichYKhoa.exe"
Name: "{autodesktop}\Dịch Y Khoa"; Filename: "{app}\DichYKhoa.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DichYKhoa.exe"; Description: "Mở Dịch Y Khoa"; Flags: nowait postinstall skipifsilent
