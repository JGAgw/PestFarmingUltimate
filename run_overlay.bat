@echo off
REM Skyblock Lane Overlay – Windows-käynnistys (venv-vaihtoehto)
REM Käyttö: kaksoisklikkaa tätä tai aja komentokehotteessa.

setlocal
cd /d "%~dp0"

REM 1) Koeta aktivoida virtuaaliympäristöä jos sellainen on
if exist .venv\Scripts\python.exe (
    set PYTHON=.venv\Scripts\python.exe
) else if exist venv\Scripts\python.exe (
    set PYTHON=venv\Scripts\python.exe
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [VIRHE] Pythonia ei loydy. Asenna Python 3.10+ (python.org).
        echo.
        pause
        exit /b 1
    )
    set PYTHON=python
)

REM 2) Tarkista että riippuvuudet on asennettu (helppo tarkistus)
%PYTHON% -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Riippuvuudet eivat ole asennettu -> asennetaan requirements.txt:sta...
    %PYTHON% -m pip install --upgrade pip
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [VIRHE] Riippuvuuksien asennus epaonnistui. Tarkista yhteys.
        pause
        exit /b 1
    )
)

REM 3) Kaynnista sovellus
echo [OK] Kaynnistetaan Skyblock Lane Overlay...
%PYTHON% main.py
endlocal
