@echo off
chcp 65001 >nul 2>nul
setlocal EnableDelayedExpansion
title Jianying to XML Converter v2.0
color 0F

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%jianying_to_xml_v2.py"
set "OUTPUT_DIR=%SCRIPT_DIR%output"
set "DRAFT_DIR="
set "JSON_ONLY="
set "PY="

:MAIN
cls
echo.
echo   ============================================
echo        Jianying Draft --^> FCP7 XML
echo        Jianying to XML Converter v2.0
echo   ============================================
echo.
if defined DRAFT_DIR (
    echo   [DRAFT] !DRAFT_DIR!
) else (
    echo   [DRAFT] NOT SET - Please select first
)
echo   [OUTPUT] !OUTPUT_DIR!
if defined JSON_ONLY (
    echo   [MODE] JSON Only
) else (
    echo   [MODE] XML + JSON
)
echo.
echo   --------------------------------------------
echo.
echo   [1] Select draft folder (paste path)
echo   [2] Auto scan drafts
echo   [3] Set output directory
echo   [4] Settings
echo   [5] START CONVERT
echo   [0] Quit
echo.
echo   --------------------------------------------
echo.
set /p "CHOICE=  > "
if not defined CHOICE goto MAIN

if "!CHOICE!"=="1" goto SELECT_PATH
if "!CHOICE!"=="2" goto AUTO_DETECT
if "!CHOICE!"=="3" goto SET_OUTPUT
if "!CHOICE!"=="4" goto SETTINGS
if "!CHOICE!"=="5" goto CONVERT
if "!CHOICE!"=="0" goto QUIT
echo.
echo   Invalid option.
timeout /t 1 >nul
goto MAIN

:: ================================================
:: Option 1: Select draft path
:: ================================================
:SELECT_PATH
cls
echo.
echo   ============================================
echo     Select Jianying Draft Path
echo   ============================================
echo.
echo   You can:
echo     - Paste the full path below
echo     - Drag a folder onto this window then press Enter
echo     - Type a keyword to search
echo.
set /p "INPUT_PATH=  > "
if not defined INPUT_PATH goto MAIN

:: Remove quotes
set "INPUT_PATH=!INPUT_PATH:"=!"

if exist "!INPUT_PATH!" (
    :: Check if it's a file
    for %%F in ("!INPUT_PATH!") do (
        if "%%~xF"==".json" (
            set "DRAFT_DIR=%%~dpF"
        ) else (
            set "DRAFT_DIR=!INPUT_PATH!"
        )
    )
    echo.
    echo   [OK] Set to: !DRAFT_DIR!
    timeout /t 2 >nul
    goto MAIN
)

:: Not found, search common paths
echo.
echo   Path not found: !INPUT_PATH!
echo   Searching...
echo.
set "FOUND="
set "SEARCH_COUNT=0"

:: Build search list from %LOCALAPPDATA%
set "P0=%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
set "P1=%LOCALAPPDATA%\JianyingPro\User Data\Projects\compositon"
set "P2=%LOCALAPPDATA%\CapCut\User Data\Projects\compositon"
set "P3=%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"

for /l %%I in (0,1,3) do (
    if exist "!P%%I!" (
        for /d %%D in ("!P%%I!\*") do (
            if exist "%%~D\draft_content.json" (
                echo %%~nxD | findstr /i "!INPUT_PATH!" >nul 2>nul
                if !errorlevel!==0 (
                    if not defined FOUND (
                        set "FOUND=%%~fD"
                        echo   Match: %%~nxD
                        echo   Path:  %%~fD
                    )
                )
            )
        )
    )
)

if defined FOUND (
    set "DRAFT_DIR=!FOUND!"
    echo.
    echo   [OK] Auto selected.
    timeout /t 2 >nul
) else (
    echo.
    echo   No match found.
    timeout /t 2 >nul
)
goto MAIN

:: ================================================
:: Option 2: Auto detect drafts
:: ================================================
:AUTO_DETECT
cls
echo.
echo   ============================================
echo     Scanning for Jianying drafts...
echo   ============================================
echo.

set "DRAFT_COUNT=0"

:: Scan LOCALAPPDATA
set "S0=%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
set "S1=%LOCALAPPDATA%\JianyingPro\User Data\Projects\compositon"
set "S2=%LOCALAPPDATA%\CapCut\User Data\Projects\compositon"
set "S3=%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"

for /l %%I in (0,1,3) do (
    if exist "!S%%I!" (
        for /d %%P in ("!S%%I!\*") do (
            if exist "%%~P\draft_content.json" (
                set /a DRAFT_COUNT+=1
                set "DRAFT_!DRAFT_COUNT!=%%~fP"
                echo   [!DRAFT_COUNT!] %%~nxP
                echo        %%~fP
                echo.
            )
        )
    )
)

:: Also scan APPDATA
set "S4=%APPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
set "S5=%APPDATA%\JianyingPro\User Data\Projects\compositon"

for /l %%I in (4,1,5) do (
    if exist "!S%%I!" (
        for /d %%P in ("!S%%I!\*") do (
            if exist "%%~P\draft_content.json" (
                set /a DRAFT_COUNT+=1
                set "DRAFT_!DRAFT_COUNT!=%%~fP"
                echo   [!DRAFT_COUNT!] %%~nxP
                echo        %%~fP
                echo.
            )
        )
    )
)

:: Check other drives (D:, E:, F:)
for %%D in (D E F) do (
    if exist "%%D:\Users" (
        for /d %%U in ("%%D:\Users\*") do (
            set "EXTRA=%%U\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
            if exist "!EXTRA!" (
                for /d %%P in ("!EXTRA!\*") do (
                    if exist "%%~P\draft_content.json" (
                        :: Check for duplicate
                        set "IS_DUP=0"
                        for /l %%J in (1,1,!DRAFT_COUNT!) do (
                            if "!DRAFT_%%J!"=="%%~fP" set "IS_DUP=1"
                        )
                        if "!IS_DUP!"=="0" (
                            set /a DRAFT_COUNT+=1
                            set "DRAFT_!DRAFT_COUNT!=%%~fP"
                            echo   [!DRAFT_COUNT!] %%~nxP
                            echo        %%~fP
                            echo.
                        )
                    )
                )
            )
        )
    )
)

if "!DRAFT_COUNT!"=="0" (
    echo   No drafts found.
    echo.
    echo   Tip: Use option [1] to manually paste the draft folder path.
    echo        Or add your path to config.json.
    echo.
    pause
    goto MAIN
)

echo   --------------------------------------------
echo   Found !DRAFT_COUNT! draft(s)
echo   --------------------------------------------
echo.
set /p "SEL=  Select [1-!DRAFT_COUNT!]: "

if not defined SEL goto MAIN
if !SEL! LSS 1 goto MAIN
if !SEL! GTR !DRAFT_COUNT! (
    echo   Invalid selection.
    timeout /t 1 >nul
    goto MAIN
)

set "DRAFT_DIR=!DRAFT_%SEL%!"
echo.
echo   [OK] Selected: !DRAFT_DIR!
timeout /t 2 >nul
goto MAIN

:: ================================================
:: Option 3: Set output directory
:: ================================================
:SET_OUTPUT
cls
echo.
echo   ============================================
echo     Set Output Directory
echo   ============================================
echo.
echo   Current: !OUTPUT_DIR!
echo.
echo   [1] Keep current
echo   [2] Script folder \output
echo   [3] Same as draft folder
echo   [4] Custom path
echo.
set /p "OPT=  > "
if not defined OPT goto MAIN

if "!OPT!"=="1" goto MAIN
if "!OPT!"=="2" (
    set "OUTPUT_DIR=%SCRIPT_DIR%output"
    echo   [OK] Set to: !OUTPUT_DIR!
    timeout /t 1 >nul
    goto MAIN
)
if "!OPT!"=="3" (
    if defined DRAFT_DIR (
        set "OUTPUT_DIR=!DRAFT_DIR!"
        echo   [OK] Set to: !OUTPUT_DIR!
    ) else (
        echo   Please select draft first.
    )
    timeout /t 1 >nul
    goto MAIN
)
if "!OPT!"=="4" (
    echo.
    set /p "NEW_DIR=  Path: "
    if defined NEW_DIR (
        set "NEW_DIR=!NEW_DIR:"=!"
        if not exist "!NEW_DIR!" mkdir "!NEW_DIR!" 2>nul
        set "OUTPUT_DIR=!NEW_DIR!"
        echo   [OK] Set to: !OUTPUT_DIR!
    )
    timeout /t 1 >nul
    goto MAIN
)
goto MAIN

:: ================================================
:: Option 4: Settings
:: ================================================
:SETTINGS
cls
echo.
echo   ============================================
echo     Settings
echo   ============================================
echo.

set "STATUS_JSON=OFF"
if defined JSON_ONLY set "STATUS_JSON=ON"

echo   [1] XML+JSON  or  JSON only : [!STATUS_JSON!]
echo   [0] Back
echo.
set /p "SET_OPT=  > "
if not defined SET_OPT goto MAIN

if "!SET_OPT!"=="0" goto MAIN
if "!SET_OPT!"=="1" (
    if defined JSON_ONLY (
        set "JSON_ONLY="
        echo   Mode: XML + JSON
    ) else (
        set "JSON_ONLY=--json-only"
        echo   Mode: JSON Only
    )
    timeout /t 1 >nul
    goto SETTINGS
)
goto SETTINGS

:: ================================================
:: Check Python
:: ================================================
:FIND_PYTHON
set "PY="
where python >nul 2>nul
if !errorlevel!==0 (
    :: Verify version
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>nul
    if !errorlevel!==0 (
        set "PY=python"
        goto :eof
    )
)
where python3 >nul 2>nul
if !errorlevel!==0 (
    python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>nul
    if !errorlevel!==0 (
        set "PY=python3"
        goto :eof
    )
)
where py >nul 2>nul
if !errorlevel!==0 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" 2>nul
    if !errorlevel!==0 (
        set "PY=py -3"
        goto :eof
    )
)
goto :eof

:: ================================================
:: Show Python installation guide
:: ================================================
:SHOW_PYTHON_GUIDE
echo.
echo   ============================================
echo     [ERROR] Python 3.8+ not found!
echo   ============================================
echo.
echo   Please install Python 3.8 or newer:
echo.
echo   Option A (Recommended):
echo     Download from https://www.python.org/downloads/
echo     IMPORTANT: Check "Add Python to PATH" during install!
echo.
echo   Option B (Microsoft Store):
echo     Open Microsoft Store, search "Python 3.x", install
echo.
echo   Option C (Winget):
echo     winget install Python.Python.3.12
echo.
echo   Option D (Scoop):
echo     scoop install python
echo.
echo   ============================================
echo.
pause
goto :eof

:: ================================================
:: Option 5: Convert
:: ================================================
:CONVERT
if not defined DRAFT_DIR (
    echo.
    echo   [ERROR] No draft selected. Use option 1 or 2 first.
    timeout /t 2 >nul
    goto MAIN
)

if not exist "!DRAFT_DIR!" (
    echo.
    echo   [ERROR] Draft directory not found: !DRAFT_DIR!
    timeout /t 2 >nul
    goto MAIN
)

:: Find Python
call :FIND_PYTHON
if not defined PY (
    call :SHOW_PYTHON_GUIDE
    goto MAIN
)

:: Ensure output dir exists
if not exist "!OUTPUT_DIR!" mkdir "!OUTPUT_DIR!"

cls
echo.
echo   ============================================
echo     Converting...
echo   ============================================
echo.
echo   Draft:  !DRAFT_DIR!
echo   Output: !OUTPUT_DIR!
echo   Python: !PY!
echo.
echo   --------------------------------------------
echo.

!PY! "!SCRIPT!" "!DRAFT_DIR!" -o "!OUTPUT_DIR!" !JSON_ONLY!
set "EXIT_CODE=!errorlevel!"

echo.
echo   --------------------------------------------
if !EXIT_CODE!==0 (
    echo.
    echo   ============================================
    echo     DONE!
    echo   ============================================
    echo.
    echo   Output: !OUTPUT_DIR!
    echo.
    if exist "!OUTPUT_DIR!\*.xml" (
        for %%F in ("!OUTPUT_DIR!\*.xml") do echo   [XML]  %%~nxF
    )
    if exist "!OUTPUT_DIR!\*_timeline.json" (
        for %%F in ("!OUTPUT_DIR!\*_timeline.json") do echo   [JSON] %%~nxF
    )
    echo.
    echo   DaVinci Resolve:
    echo   File -^> Import Timeline -^> Import AAF, EDL, XML...
    echo.
    set /p "OPEN_DIR=  Open output folder? (Y/n): "
    if /i not "!OPEN_DIR!"=="n" (
        explorer "!OUTPUT_DIR!"
    )
) else (
    echo.
    echo   [ERROR] Conversion failed. Exit code: !EXIT_CODE!
)

echo.
pause
goto MAIN

:QUIT
cls
endlocal
exit /b 0
