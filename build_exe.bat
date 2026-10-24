@echo off
rem Builds Platinum3DSBuilder.exe (run once on Windows with Python 3 installed from python.org).
rem The exe carries its own copy of p3ds_build.sh and apply_worktree.py, so it can live anywhere.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --onefile --windowed --name Platinum3DSBuilder ^
  --add-data "p3ds_build.sh;." --add-data "apply_worktree.py;." p3ds_builder.py || goto :err
copy /y "dist\Platinum3DSBuilder.exe" "Platinum3DSBuilder.exe" >nul
echo.
echo Done: %~dp0Platinum3DSBuilder.exe
echo Double-click it, pick your Platinum .nds and your SD card, press "Build and install".
echo After you change the build scripts, run this again so the exe picks up the new copies.
pause
exit /b 0
:err
echo Build failed - see messages above.
pause
exit /b 1
