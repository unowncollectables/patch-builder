@echo off
rem Builds Pokemon3DSBuilder.exe (run once on Windows with Python 3 installed from python.org).
rem The exe carries its own copy of the build scripts, so it can live anywhere.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name Pokemon3DSBuilder ^
  --add-data "p3ds_build.sh;." --add-data "ss_build.sh;." --add-data "p3ds_get.sh;." --add-data "build_cia_tools.sh;." --add-data "apply_worktree.py;." p3ds_builder.py || goto :err
copy /y "dist\Pokemon3DSBuilder.exe" "Pokemon3DSBuilder.exe" >nul
echo.
echo Done: %~dp0Pokemon3DSBuilder.exe
echo Double-click it, tick your game(s), pick your .nds file(s) and your SD card, press "Build and install".
echo After you change the build scripts, run this again so the exe picks up the new copies.
pause
exit /b 0
:err
echo Build failed - see messages above.
pause
exit /b 1
