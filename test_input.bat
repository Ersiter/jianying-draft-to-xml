@echo off
chcp 65001 >nul 2>nul
setlocal EnableDelayedExpansion

echo ==========================================
echo   MINIMAL INPUT TEST
echo ==========================================
echo.
echo   Type 1, 2, 3, or anything.
echo   Type quit to exit.
echo.

:LOOP
set /p "CHOICE=INPUT> "
echo   You typed: [!CHOICE!]
echo.
if "!CHOICE!"=="quit" goto END
if "!CHOICE!"=="1" (
    echo   >> Matched 1
) else if "!CHOICE!"=="2" (
    echo   >> Matched 2
) else if "!CHOICE!"=="3" (
    echo   >> Matched 3
) else (
    echo   >> Invalid
)
echo.
goto LOOP

:END
echo Bye.
