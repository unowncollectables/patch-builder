@echo off
rem Builds Platinum3DSBuilder.exe (run once on Windows with Python 3 installed from python.org).
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --onefile --windowed --name Platinum3DSBuilder p3ds_builder.py || goto :err
echo.
echo Done: %~dp0dist\Platinum3DSBuilder.exe
echo Keep the exe anywhere; it asks for your project folder (the clone that contains this builder folder).
pause
exit /b 0
:err
echo Build failed - see messages above.
pause
exit /b 1
