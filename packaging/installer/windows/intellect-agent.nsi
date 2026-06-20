; Intellect Agent — Windows NSIS Installer
; -----------------------------------------------
; Build with: makensis -DSOURCE_DIR=/path/to/bundle -DOUTPUT_DIR=/output intellect-agent.nsi

!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.0.0"
!endif

!define PRODUCT_NAME "Intellect Agent"
!define PRODUCT_PUBLISHER "ONTOWEB"
!define PRODUCT_WEB_SITE "https://gitee.com/ontoweb/intellect-agent"
!define INSTALL_DIR "$LOCALAPPDATA\IntellectAgent"

; ── Modern UI ────────────────────────────────────────────────────────────
!include "MUI2.nsh"
!include "FileFunc.nsh"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ── Installer settings ─────────────────────────────────────────────────────
Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "${OUTPUT_DIR}\Intellect-Agent-${PRODUCT_VERSION}-Setup.exe"
InstallDir "${INSTALL_DIR}"
RequestExecutionLevel user
ShowInstDetails show

; ── Section: Install ───────────────────────────────────────────────────────
Section "Install"

  SetOutPath "$INSTDIR"

  ; Copy entire bundle from source directory
  File /r /x ".git" /x "*.pyc" /x "__pycache__" "${SOURCE_DIR}\*"

  ; Verify venv exists
  IfFileExists "$INSTDIR\venv\Scripts\python.exe" 0 +3
    DetailPrint "Python venv: OK"
    Goto +2
    DetailPrint "WARNING: Python venv not found"

  ; Verify Node.js exists
  IfFileExists "$INSTDIR\node-runtime\bin\node.exe" 0 +3
    DetailPrint "Node.js runtime: OK"
    Goto +2
    DetailPrint "INFO: Node.js not bundled (optional)"

  ; Create Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\Intellect Agent"
  CreateShortCut "$SMPROGRAMS\Intellect Agent\Intellect CLI.lnk" \
    "cmd.exe" '/k ""$INSTDIR\venv\Scripts\activate.bat" && intellect chat"' \
    "$SYSDIR\cmd.exe" 0

  CreateShortCut "$SMPROGRAMS\Intellect Agent\Intellect Gateway.lnk" \
    "cmd.exe" '/k ""$INSTDIR\venv\Scripts\activate.bat" && intellect gateway start"' \
    "$SYSDIR\cmd.exe" 0

  CreateShortCut "$SMPROGRAMS\Intellect Agent\Uninstall Intellect Agent.lnk" \
    "$INSTDIR\uninstall.exe"

  ; Register PATH addition (user scope, requires restart to take effect)
  ; For immediate use, the shortcut above activates the venv
  ${EnVar::AddEnvVar} "PATH" "$INSTDIR\bin"

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Registry for Add/Remove Programs
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent" \
    "NoRepair" 1

SectionEnd

; ── Section: Uninstall ─────────────────────────────────────────────────────
Section "Uninstall"

  ; Remove Start Menu entries
  Delete "$SMPROGRAMS\Intellect Agent\Intellect CLI.lnk"
  Delete "$SMPROGRAMS\Intellect Agent\Intellect Gateway.lnk"
  Delete "$SMPROGRAMS\Intellect Agent\Uninstall Intellect Agent.lnk"
  RMDir "$SMPROGRAMS\Intellect Agent"

  ; Remove registry
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntellectAgent"

  ; Remove install directory
  RMDir /r "$INSTDIR"

SectionEnd
