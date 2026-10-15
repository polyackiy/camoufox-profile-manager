; The same prerequisite logic is compiled into production and isolated fixtures.
; Registry paths and the bundled bootstrapper are build-time constants only.
!macro WebView2Prerequisite MACHINE_HIVE MACHINE_KEY USER_KEY BOOTSTRAPPER
Function IsWebView2Version
    ; Input/output: $0. A nonzero four-component numeric version is required.
    ; Reject malformed/stale registration instead of declaring the UI ready.
    Push $1
    Push $2
    Push $3
    Push $4
    Push $5
    StrLen $1 $0
    IntCmp $1 23 0 0 invalid_version
    StrCpy $1 0
    StrCpy $2 0 ; number of separators
    StrCpy $3 0 ; digits in current component
    StrCpy $4 0 ; at least one nonzero digit
version_next:
    StrCpy $5 $0 1 $1
    StrCmp $5 "" version_end
    IntOp $1 $1 + 1
    StrCmp $5 "." version_separator
    StrCmp $5 "0" version_digit
    StrCmp $5 "1" version_nonzero
    StrCmp $5 "2" version_nonzero
    StrCmp $5 "3" version_nonzero
    StrCmp $5 "4" version_nonzero
    StrCmp $5 "5" version_nonzero
    StrCmp $5 "6" version_nonzero
    StrCmp $5 "7" version_nonzero
    StrCmp $5 "8" version_nonzero
    StrCmp $5 "9" version_nonzero invalid_version
version_nonzero:
    StrCpy $4 1
version_digit:
    IntOp $3 $3 + 1
    IntCmp $3 5 version_next version_next invalid_version
version_separator:
    IntCmp $3 0 invalid_version
    IntOp $2 $2 + 1
    IntCmp $2 3 0 0 invalid_version
    StrCpy $3 0
    Goto version_next
version_end:
    IntCmp $2 3 0 invalid_version invalid_version
    IntCmp $3 0 invalid_version
    StrCpy $0 $4
    Goto version_done
invalid_version:
    StrCpy $0 0
version_done:
    Pop $5
    Pop $4
    Pop $3
    Pop $2
    Pop $1
FunctionEnd

Function DetectWebView2
    SetRegView 32
    ReadRegStr $0 ${MACHINE_HIVE} "${MACHINE_KEY}" "pv"
    Call IsWebView2Version
    StrCmp $0 1 detected
    ReadRegStr $0 HKCU "${USER_KEY}" "pv"
    Call IsWebView2Version
detected:
FunctionEnd

Function CheckWebView2
retry_runtime:
    ; Recheck on every retry: an administrator/parallel installer may have
    ; installed the runtime while the user was fixing the network connection.
    Call DetectWebView2
    StrCmp $0 1 runtime_ready
    DetailPrint "Installing Microsoft WebView2 Runtime (internet access required)..."
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    ClearErrors
    File /oname=MicrosoftEdgeWebview2Setup.exe "${BOOTSTRAPPER}"
    IfErrors runtime_failed
    ClearErrors
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install' $1
    IfErrors runtime_failed
    StrCmp $1 "0" 0 runtime_failed
    Call DetectWebView2
    StrCmp $0 1 runtime_ready
runtime_failed:
    MessageBox MB_ICONSTOP|MB_RETRYCANCEL "Microsoft WebView2 is not ready. Check your internet connection or contact your administrator, then choose Retry. Cancel leaves the manager's existing files unchanged. Silent installation exits with code 4." /SD IDCANCEL IDRETRY retry_runtime
    SetErrorLevel 4
    Abort
runtime_ready:
    ClearErrors
FunctionEnd
!macroend
