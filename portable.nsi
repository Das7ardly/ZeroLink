; ZeroLink portable: unpacks the app into a folder you choose. No install, no registry, no shortcuts.
; Build:  makensis -DVERSION=2.1.0 portable.nsi
Unicode true
Target amd64-unicode
!ifndef VERSION
  !define VERSION "3.1.0"
!endif
Name "ZeroLink Download Toolkit (portable)"
OutFile "ZeroLink-Portable-${VERSION}.exe"
InstallDir "$DESKTOP\ZeroLink Portable"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "ZeroLink ${VERSION} portable"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "ZeroLink Portable"
VIAddVersionKey "FileDescription" "ZeroLink portable (unpacks to a folder)"
VIAddVersionKey "CompanyName" "ZeroLink"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "ZeroLink"

!include "MUI2.nsh"
!define MUI_ICON "assets\icon.ico"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Choose where to unpack ZeroLink. A USB stick works too. Nothing is installed: settings and tools stay in a data folder next to ZeroLink.exe, so to remove it just delete the folder."
!define MUI_FINISHPAGE_RUN "$INSTDIR\ZeroLink.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ZeroLink now"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\_internal"
  File /r "dist\ZeroLink\*.*"
  File "assets\portable.txt"   ; keep settings and tools in .\data next to the exe
SectionEnd
