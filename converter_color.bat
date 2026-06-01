@echo off
chcp 65001 >nul 2>nul
setlocal EnableDelayedExpansion

:: ── INIT: Get ESC char via Python (no PowerShell, no stdin pollution) ──
:: Write ESC byte (0x1B) to temp file, read back
set "ESC_TEMP=%TEMP%\_jy_esc.tmp"
python -c "import sys; sys.stdout.buffer.write(b'\x1b')" 2>nul >"%ESC_TEMP%"
set "ESC="
for /f "usebackq delims=" %%A in ("%ESC_TEMP%") do (
    if not defined ESC set "ESC=%%A"
)
del "%ESC_TEMP%" 2>nul

:: Define ANSI codes
set "A_RESET=%ESC%[0m"
set "A_BOLD=%ESC%[1m"
set "A_RED=%ESC%[31m"
set "A_GREEN=%ESC%[32m"
set "A_YELLOW=%ESC%[33m"
set "A_BLUE=%ESC%[34m"
set "A_CYAN=%ESC%[36m"
set "A_DIM=%ESC%[90m"
set "A_BOLD_CYAN=%ESC%[1;36m"
set "A_BOLD_GREEN=%ESC%[1;32m"
set "A_BOLD_YELLOW=%ESC%[1;33m"
set "A_BOLD_RED=%ESC%[1;31m"
set "A_BOLD_BLUE=%ESC%[1;34m"
set "A_BOLD_WHITE=%ESC%[1;37m"

title Jianying-CapCut2XML v2.0
color 0F

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%jianying_to_xml.py"
set "OUTPUT_DIR=%SCRIPT_DIR%output"
set "DRAFT_DIR="
set "JSON_ONLY="
set "PY="

:MAIN
cls
echo.
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%     Jianying Draft --^> FCP7 XML%A_RESET%
echo   %A_BOLD_CYAN%     Jianying-CapCut2XML v2.0%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.
if defined DRAFT_DIR (
    echo   %A_BOLD_WHITE%[DRAFT]%A_RESET% %A_GREEN%!DRAFT_DIR!%A_RESET%
) else (
    echo   %A_BOLD_WHITE%[DRAFT]%A_RESET% %A_YELLOW%NOT SET%A_RESET% %A_DIM%- Please select first%A_RESET%
)
echo   %A_BOLD_WHITE%[OUTPUT]%A_RESET% !OUTPUT_DIR!
if defined JSON_ONLY (
    echo   %A_BOLD_WHITE%[MODE]%A_RESET% %A_CYAN%JSON Only%A_RESET%
) else (
    echo   %A_BOLD_WHITE%[MODE]%A_RESET% XML + JSON
)
echo.
echo   %A_DIM%--------------------------------------------%A_RESET%
echo.
echo   %A_BOLD_CYAN%[1]%A_RESET% Select draft folder %A_DIM%(paste path)%A_RESET%
echo   %A_BOLD_CYAN%[2]%A_RESET% Auto scan drafts
echo   %A_BOLD_CYAN%[3]%A_RESET% Set output directory
echo   %A_BOLD_CYAN%[4]%A_RESET% Settings
echo   %A_BOLD_GREEN%[5]%A_RESET% %A_BOLD_WHITE%START CONVERT%A_RESET%
echo   %A_DIM%[0]%A_RESET% Quit
echo.
echo   %A_DIM%--------------------------------------------%A_RESET%
echo.
set /p "CHOICE=> "
if not defined CHOICE goto MAIN

if "!CHOICE!"=="1" goto SELECT_PATH
if "!CHOICE!"=="2" goto AUTO_DETECT
if "!CHOICE!"=="3" goto SET_OUTPUT
if "!CHOICE!"=="4" goto SETTINGS
if "!CHOICE!"=="5" goto CONVERT
if "!CHOICE!"=="0" goto QUIT
echo.
echo   %A_BOLD_RED%Invalid option.%A_RESET%
timeout /t 1 >nul
goto MAIN

:: ================================================
:: Option 1: Select draft path
:: ================================================
:SELECT_PATH
cls
echo.
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%  Select Jianying Draft Path%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.
echo   %A_DIM%You can:%A_RESET%
echo   %A_DIM%  - Paste the full path below%A_RESET%
echo   %A_DIM%  - Drag a folder onto this window then press Enter%A_RESET%
echo   %A_DIM%  - Type a keyword to search%A_RESET%
echo.
set /p "INPUT_PATH=> "
if not defined INPUT_PATH goto MAIN

set "INPUT_PATH=!INPUT_PATH:"=!"

if exist "!INPUT_PATH!" (
    for %%F in ("!INPUT_PATH!") do (
        if "%%~xF"==".json" (
            set "DRAFT_DIR=%%~dpF"
        ) else (
            set "DRAFT_DIR=!INPUT_PATH!"
        )
    )
    echo.
    echo   %A_BOLD_GREEN%[OK]%A_RESET% Set to: !DRAFT_DIR!
    timeout /t 2 >nul
    goto MAIN
)

echo.
echo   %A_YELLOW%Path not found: !INPUT_PATH!%A_RESET%
echo   %A_DIM%Searching...%A_RESET%
echo.
set "FOUND="

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
                        echo   %A_BOLD_GREEN%Match:%A_RESET% %%~nxD
                        echo   %A_DIM%  Path: %%~fD%A_RESET%
                    )
                )
            )
        )
    )
)

if defined FOUND (
    set "DRAFT_DIR=!FOUND!"
    echo.
    echo   %A_BOLD_GREEN%[OK]%A_RESET% Auto selected.
    timeout /t 2 >nul
) else (
    echo.
    echo   %A_RED%No match found.%A_RESET%
    timeout /t 2 >nul
)
goto MAIN

:: ================================================
:: Option 2: Auto detect drafts
:: ================================================
:AUTO_DETECT
cls
echo.
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%  Scanning for Jianying drafts...%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.

set "DRAFT_COUNT=0"

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
                echo   %A_BOLD_CYAN%[!DRAFT_COUNT!]%A_RESET% %%~nxP
                echo   %A_DIM%       %%~fP%A_RESET%
                echo.
            )
        )
    )
)

set "S4=%APPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"
set "S5=%APPDATA%\JianyingPro\User Data\Projects\compositon"

for /l %%I in (4,1,5) do (
    if exist "!S%%I!" (
        for /d %%P in ("!S%%I!\*") do (
            if exist "%%~P\draft_content.json" (
                set /a DRAFT_COUNT+=1
                set "DRAFT_!DRAFT_COUNT!=%%~fP"
                echo   %A_BOLD_CYAN%[!DRAFT_COUNT!]%A_RESET% %%~nxP
                echo   %A_DIM%       %%~fP%A_RESET%
                echo.
            )
        )
    )
)

for %%D in (D E F) do (
    if exist "%%D:\Users" (
        for /d %%U in ("%%D:\Users\*") do (
            set "EXTRA=%%U\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
            if exist "!EXTRA!" (
                for /d %%P in ("!EXTRA!\*") do (
                    if exist "%%~P\draft_content.json" (
                        set "IS_DUP=0"
                        for /l %%J in (1,1,!DRAFT_COUNT!) do (
                            if "!DRAFT_%%J!"=="%%~fP" set "IS_DUP=1"
                        )
                        if "!IS_DUP!"=="0" (
                            set /a DRAFT_COUNT+=1
                            set "DRAFT_!DRAFT_COUNT!=%%~fP"
                            echo   %A_BOLD_CYAN%[!DRAFT_COUNT!]%A_RESET% %%~nxP
                            echo   %A_DIM%       %%~fP%A_RESET%
                            echo.
                        )
                    )
                )
            )
        )
    )
)

if "!DRAFT_COUNT!"=="0" (
    echo   %A_BOLD_RED%No drafts found.%A_RESET%
    echo.
    echo   %A_DIM%Tip: Use option [1] to manually paste the draft folder path.%A_RESET%
    echo   %A_DIM%     Or add your path to config.json.%A_RESET%
    echo.
    pause
    goto MAIN
)

echo   %A_DIM%--------------------------------------------%A_RESET%
echo   %A_BOLD_WHITE%Found !DRAFT_COUNT! draft(s)%A_RESET%
echo   %A_DIM%--------------------------------------------%A_RESET%
echo.
set /p "SEL=> "
if not defined SEL goto MAIN
if !SEL! LSS 1 goto MAIN
if !SEL! GTR !DRAFT_COUNT! (
    echo   %A_BOLD_RED%Invalid selection.%A_RESET%
    timeout /t 1 >nul
    goto MAIN
)

set "DRAFT_DIR=!DRAFT_%SEL%!"
echo.
echo   %A_BOLD_GREEN%[OK]%A_RESET% Selected: !DRAFT_DIR!
timeout /t 2 >nul
goto MAIN

:: ================================================
:: Option 3: Set output directory
:: ================================================
:SET_OUTPUT
cls
echo.
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%  Set Output Directory%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.
echo   %A_DIM%Current:%A_RESET% !OUTPUT_DIR!
echo.
echo   %A_DIM%[1]%A_RESET% Keep current
echo   %A_DIM%[2]%A_RESET% Script folder \output
echo   %A_DIM%[3]%A_RESET% Same as draft folder
echo   %A_DIM%[4]%A_RESET% Custom path
echo.
set /p "OPT=> "
if not defined OPT goto MAIN

if "!OPT!"=="1" goto MAIN
if "!OPT!"=="2" (
    set "OUTPUT_DIR=%SCRIPT_DIR%output"
    echo   %A_BOLD_GREEN%[OK]%A_RESET% Set to: !OUTPUT_DIR!
    timeout /t 1 >nul
    goto MAIN
)
if "!OPT!"=="3" (
    if defined DRAFT_DIR (
        set "OUTPUT_DIR=!DRAFT_DIR!"
        echo   %A_BOLD_GREEN%[OK]%A_RESET% Set to: !OUTPUT_DIR!
    ) else (
        echo   %A_YELLOW%Please select draft first.%A_RESET%
    )
    timeout /t 1 >nul
    goto MAIN
)
if "!OPT!"=="4" (
    echo.
    set /p "NEW_DIR=Path: "
    if defined NEW_DIR (
        set "NEW_DIR=!NEW_DIR:"=!"
        if not exist "!NEW_DIR!" mkdir "!NEW_DIR!" 2>nul
        set "OUTPUT_DIR=!NEW_DIR!"
        echo   %A_BOLD_GREEN%[OK]%A_RESET% Set to: !OUTPUT_DIR!
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
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%  Settings%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.

set "STATUS_JSON=OFF"
if defined JSON_ONLY set "STATUS_JSON=%A_CYAN%ON%A_RESET%"

echo   %A_DIM%[1]%A_RESET% XML+JSON  or  JSON only : [!STATUS_JSON!]
echo   %A_DIM%[0]%A_RESET% Back
echo.
set /p "SET_OPT=> "
if not defined SET_OPT goto MAIN

if "!SET_OPT!"=="0" goto MAIN
if "!SET_OPT!"=="1" (
    if defined JSON_ONLY (
        set "JSON_ONLY="
        echo   %A_BOLD_GREEN%Mode: XML + JSON%A_RESET%
    ) else (
        set "JSON_ONLY=--json-only"
        echo   %A_CYAN%Mode: JSON Only%A_RESET%
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
echo   %A_BOLD_RED%=============================================%A_RESET%
echo   %A_BOLD_RED%  [ERROR] Python 3.8+ not found!%A_RESET%
echo   %A_BOLD_RED%=============================================%A_RESET%
echo.
echo   %A_BOLD_WHITE%Please install Python 3.8 or newer:%A_RESET%
echo.
echo   %A_BOLD_CYAN%Option A (Recommended):%A_RESET%
echo     https://www.python.org/downloads/
echo     %A_YELLOW%IMPORTANT: Check "Add Python to PATH" during install!%A_RESET%
echo.
echo   %A_BOLD_CYAN%Option B (Microsoft Store):%A_RESET%
echo     Open Microsoft Store, search "Python 3.x", install
echo.
echo   %A_BOLD_CYAN%Option C (Winget):%A_RESET%
echo     winget install Python.Python.3.12
echo.
echo   %A_BOLD_CYAN%Option D (Scoop):%A_RESET%
echo     scoop install python
echo.
echo   %A_BOLD_RED%=============================================%A_RESET%
echo.
pause
goto :eof

:: ================================================
:: Option 5: Convert
:: ================================================
:CONVERT
if not defined DRAFT_DIR (
    echo.
    echo   %A_BOLD_RED%[ERROR]%A_RESET% No draft selected. Use option 1 or 2 first.
    timeout /t 2 >nul
    goto MAIN
)

if not exist "!DRAFT_DIR!" (
    echo.
    echo   %A_BOLD_RED%[ERROR]%A_RESET% Draft directory not found: !DRAFT_DIR!
    timeout /t 2 >nul
    goto MAIN
)

call :FIND_PYTHON
if not defined PY (
    call :SHOW_PYTHON_GUIDE
    goto MAIN
)

if not exist "!OUTPUT_DIR!" mkdir "!OUTPUT_DIR!"

cls
echo.
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo   %A_BOLD_CYAN%  Converting...%A_RESET%
echo   %A_BOLD_BLUE%=============================================%A_RESET%
echo.
echo   %A_BOLD_WHITE%Draft:%A_RESET%  !DRAFT_DIR!
echo   %A_BOLD_WHITE%Output:%A_RESET% !OUTPUT_DIR!
echo   %A_BOLD_WHITE%Python:%A_RESET% !PY!
echo.
echo   %A_DIM%--------------------------------------------%A_RESET%
echo.

!PY! "!SCRIPT!" "!DRAFT_DIR!" -o "!OUTPUT_DIR!" !JSON_ONLY!
set "EXIT_CODE=!errorlevel!"

echo.
echo   %A_DIM%--------------------------------------------%A_RESET%
if !EXIT_CODE!==0 (
    echo.
    echo   %A_BOLD_GREEN%=============================================%A_RESET%
    echo   %A_BOLD_GREEN%  DONE!%A_RESET%
    echo   %A_BOLD_GREEN%=============================================%A_RESET%
    echo.
    echo   %A_BOLD_WHITE%Output:%A_RESET% !OUTPUT_DIR!
    echo.
    if exist "!OUTPUT_DIR!\*.xml" (
        for %%F in ("!OUTPUT_DIR!\*.xml") do echo   %A_GREEN%[XML]%A_RESET%  %%~nxF
    )
    if exist "!OUTPUT_DIR!\*_timeline.json" (
        for %%F in ("!OUTPUT_DIR!\*_timeline.json") do echo   %A_GREEN%[JSON]%A_RESET% %%~nxF
    )
    echo.
    echo   %A_DIM%DaVinci Resolve:%A_RESET%
    echo   %A_DIM%File -^> Import Timeline -^> Import AAF, EDL, XML...%A_RESET%
    echo.
    set /p "OPEN_DIR=Open output folder? (Y/n): "
    if /i not "!OPEN_DIR!"=="n" (
        explorer "!OUTPUT_DIR!"
    )
) else (
    echo.
    echo   %A_BOLD_RED%[ERROR]%A_RESET% Conversion failed. Exit code: !EXIT_CODE!
)

echo.
pause
goto MAIN

:QUIT
cls
endlocal
exit /b 0
