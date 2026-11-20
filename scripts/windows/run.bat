@echo off
rem Privacy Scanner - Windows 실행 launcher
rem   scripts\windows\run.bat                      : GUI
rem   scripts\windows\run.bat --file sample.csv    : CLI 분석
rem   scripts\windows\run.bat --no-ai --file a.csv : 규칙 엔진만
rem
rem 우선 dist\PrivacyScanner.exe (Python 없이 실행) 를 사용하고,
rem 없으면 python -m app.main 으로 실행한다.

setlocal
set ROOT=%~dp0..\..
set EXE=%ROOT%\dist\PrivacyScanner.exe
set OUT=%ROOT%\results\windows

if not exist "%OUT%" mkdir "%OUT%"

if exist "%EXE%" (
    echo [run] %EXE%
    "%EXE%" %*
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    echo [run] python -m app.main  (EXE 없음: python으로 실행)
    cd /d "%ROOT%"
    python -m app.main %*
    goto :end
)

echo [error] PrivacyScanner.exe 도 python 도 찾을 수 없습니다.
echo         scripts\windows\install-offline.ps1 로 오프라인 설치 후 scripts\windows\build-exe.ps1 로 EXE 를 만드세요.
exit /b 1

:end
endlocal
