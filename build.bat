@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt || exit /b 1
python -m pytest -q tests || exit /b 1
python -c "from app import make_icon; make_icon(256).save('konsol.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])" || exit /b 1
rem build/ вне OneDrive: синхронизация блокирует временные файлы PyInstaller.
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name Konsol --icon "%~dp0konsol.ico" ^
    --version-file "%~dp0version_info.txt" ^
    --workpath "%TEMP%\konsol_build" --specpath "%TEMP%\konsol_build" --distpath "%~dp0dist" ^
    --collect-all vgamepad --collect-all customtkinter --hidden-import pystray._win32 "%~dp0app.py" || exit /b 1
echo Done: dist\Konsol.exe  (run it once to install)
