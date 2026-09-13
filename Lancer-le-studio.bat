@echo off
REM Raccourci Windows : double-cliquer ce fichier lance le studio.
cd /d "%~dp0"
title Studio Omnipotard

REM On essaie py puis python, et on VERIFIE que l'interpreteur repond : sous
REM Windows, "python" peut n'etre qu'un raccourci vers le Microsoft Store, qui
REM ouvre la boutique au lieu de signaler que Python manque.
set "PY="
for %%C in (py python python3) do (
    if not defined PY (
        %%C -c "import sys" >nul 2>&1 && set "PY=%%C"
    )
)

if not defined PY (
    echo.
    echo   Python n'est pas installe sur cette machine.
    echo.
    echo   A telecharger ici : https://www.python.org/downloads/
    echo   Pendant l'installation, COCHEZ "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM numpy est la seule bibliotheque a installer ; on la pose si elle manque
%PY% -c "import numpy" >nul 2>&1
if errorlevel 1 (
    echo Installation de numpy ^(une seule fois, patientez^)...
    %PY% -m pip install --quiet numpy
    if errorlevel 1 (
        echo.
        echo   L'installation a echoue. Essayez dans une invite de commandes :
        echo       %PY% -m pip install numpy
        echo.
        pause
        exit /b 1
    )
)

echo.
echo   Demarrage du studio. LAISSEZ CETTE FENETRE OUVERTE.
echo   Pour arreter : fermez cette fenetre.
echo.
%PY% tools\studio.py

echo.
echo   Le studio s'est arrete.
pause
