@echo off
cd /d "%~dp0"
echo Baue VOIDMETER.exe ...
py -m pip install --upgrade pyinstaller || python -m pip install --upgrade pyinstaller
py -m PyInstaller --onefile --noconsole --name VOIDMETER voidmeter.py || python -m PyInstaller --onefile --noconsole --name VOIDMETER voidmeter.py
echo.
echo Fertig: dist\VOIDMETER.exe
pause
