Unicode True
!include "MUI2.nsh"
Name "Camoufox Profile Manager"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Camoufox Profile Manager"
RequestExecutionLevel user
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\camoufox-pm.exe"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Russian"
Function CheckWebView2
    SetRegView 32
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    StrCmp $0 "" check_user
    StrCmp $0 "0.0.0.0" check_user runtime_ready
check_user:
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    StrCmp $0 "" install_runtime
    StrCmp $0 "0.0.0.0" install_runtime runtime_ready
install_runtime:
    DetailPrint "Installing Microsoft WebView2 Runtime (internet access required)..."
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File /oname=MicrosoftEdgeWebview2Setup.exe "${WEBVIEW2}"
    ExecWait '$"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe$" /silent /install' $1
    StrCmp $1 "0" runtime_ready
    MessageBox MB_ICONSTOP "Microsoft WebView2 could not be installed. Check your connection or contact your administrator, then run this installer again."
    Abort
runtime_ready:
FunctionEnd
Section "Install"
    Call CheckWebView2
    SetOutPath "$INSTDIR"
    File /r "${BUNDLE}\*"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    CreateDirectory "$SMPROGRAMS\Camoufox Profile Manager"
    CreateShortcut "$SMPROGRAMS\Camoufox Profile Manager\Camoufox Profile Manager.lnk" "$INSTDIR\camoufox-pm.exe"
    CreateShortcut "$SMPROGRAMS\Camoufox Profile Manager\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "DisplayName" "Camoufox Profile Manager"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "UninstallString" '$"$INSTDIR\Uninstall.exe$"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "NoRepair" 1
SectionEnd
Section "Uninstall"
    Delete "$SMPROGRAMS\Camoufox Profile Manager\Camoufox Profile Manager.lnk"
    Delete "$SMPROGRAMS\Camoufox Profile Manager\Uninstall.lnk"
    RMDir "$SMPROGRAMS\Camoufox Profile Manager"
    RMDir /r "$INSTDIR"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM"
    ; Keep profiles, encryption key and backups in the separate user data directory.
SectionEnd
