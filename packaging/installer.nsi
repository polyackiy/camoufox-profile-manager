Unicode True
; Preserve the pinned fpgen model dates through installation.
SetDateSave on
!include "MUI2.nsh"
!include "${UNINSTALL_MANIFEST}"
Name "Camoufox Profile Manager"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Camoufox Profile Manager"
RequestExecutionLevel user
!define MUI_ABORTWARNING
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\camoufox-pm.exe"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Russian"
; Validate before any install/uninstall writes. Refuse redirected known paths
; instead of following junctions/symlinks into unrelated data. This is a per-user
; installer, not a boundary against concurrent mutation by the same OS account.
!macro PathGuards PREFIX
Function ${PREFIX}ValidatePath
    Exch $0
    Push $1
    System::Call 'kernel32::GetFileAttributesW(w r0) i.r1'
    IntCmp $1 -1 missing
    IntOp $1 $1 & 0x400
    IntCmp $1 0 valid unsafe unsafe
missing:
    System::Call 'kernel32::GetLastError() i.r1'
    IntCmp $1 2 valid
    IntCmp $1 3 valid unsafe unsafe
unsafe:
    MessageBox MB_ICONSTOP|MB_OK "Installation or removal stopped: an application path is redirected or inaccessible. Restore the original application folders and try again. Your data has not been removed." /SD IDOK
    SetErrorLevel 2
    Abort
valid:
    Pop $1
    Pop $0
FunctionEnd
Function ${PREFIX}ValidateAncestors
    Exch $0
    Push $1
next_parent:
    Push $0
    Call ${PREFIX}ValidatePath
    GetFullPathName $1 "$0\.."
    StrCmp $1 $0 done
    StrCmp $1 "" done
    StrCpy $0 $1
    Goto next_parent
done:
    Pop $1
    Pop $0
FunctionEnd
!macroend
!insertmacro PathGuards ""
!insertmacro PathGuards "un."

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
    !insertmacro ValidateBundlePaths ""
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
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "DisplayIcon" "$INSTDIR\camoufox-pm.exe,0"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM" "NoRepair" 1
SectionEnd
!macro RemoveOwnedFile PATH
    ClearErrors
    Delete "${PATH}"
    IfErrors 0 +4
    MessageBox MB_ICONSTOP|MB_OK "An application file could not be removed. Close the manager and its browsers, then run the uninstaller again. Your data is preserved." /SD IDOK
    SetErrorLevel 3
    Abort
!macroend
Section "Uninstall"
    !insertmacro ValidateBundlePaths "un."
    !insertmacro RemoveBundleFiles
    Delete "$SMPROGRAMS\Camoufox Profile Manager\Camoufox Profile Manager.lnk"
    Delete "$SMPROGRAMS\Camoufox Profile Manager\Uninstall.lnk"
    RMDir "$SMPROGRAMS\Camoufox Profile Manager"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CamoufoxPM"
    ; Keep profiles, encryption key and backups in the separate user data directory.
SectionEnd
