@echo off
REM Builds dist\EasyRemix\ (EasyRemix.exe + _internal folder) on Windows. Requires Python 3.10+ (python.org) on PATH.
setlocal
set DENO_VERSION=v2.5.6
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements.txt pyinstaller || goto :error
if not exist bin\deno.exe (
  echo Downloading deno %DENO_VERSION% ...
  if not exist bin mkdir bin
  powershell -NoProfile -Command "Invoke-WebRequest https://github.com/denoland/deno/releases/download/%DENO_VERSION%/deno-x86_64-pc-windows-msvc.zip -OutFile deno.zip; Expand-Archive deno.zip -DestinationPath bin -Force; Remove-Item deno.zip" || goto :error
)
python -m PyInstaller EasyRemix.spec --noconfirm || goto :error
echo.
echo Done! Your program is in the folder dist\EasyRemix\
echo Start it with dist\EasyRemix\EasyRemix.exe (keep the _internal folder next to it).
goto :eof
:error
echo Build failed.
exit /b 1
