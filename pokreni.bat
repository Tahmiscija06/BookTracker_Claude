@echo off
REM Prečica za Windows: instalira Flask (ako treba) i pokreće aplikaciju.
cd /d "%~dp0"
python -m pip install -r requirements.txt
python app.py
pause
