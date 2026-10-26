@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw voidmeter.py %* & exit /b)
where pyw >nul 2>nul && (start "" pyw voidmeter.py %* & exit /b)
echo Python wurde nicht gefunden.
echo Bitte Python 3 von https://www.python.org/downloads/ installieren
echo (beim Setup "Add python.exe to PATH" anhaken) und erneut starten.
pause
