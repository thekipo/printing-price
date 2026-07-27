@echo off
setlocal

echo === Building Cenniki_StepUp.exe ===

if not exist venv (
    python -m venv venv
)

call venv\Scripts\activate.bat

pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed ^
    --collect-data customtkinter ^
    --name "Cenniki_StepUp" ^
    app.py

echo.
echo Done! File: dist\Cenniki_StepUp.exe
pause
