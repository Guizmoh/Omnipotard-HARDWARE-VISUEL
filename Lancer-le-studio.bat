@echo off
REM Raccourci Windows : double-cliquer ce fichier lance le studio.
chcp 65001 >nul
cd /d "%~dp0"

set PY=
where py >nul 2>&1 && set PY=py
if "%PY%"=="" (where python >nul 2>&1 && set PY=python)
if "%PY%"=="" (
    echo Python n'est pas installe.
    echo A telecharger sur https://www.python.org/downloads/
    echo Pensez a cocher "Add Python to PATH" pendant l'installation.
    echo.
    pause
    exit /b 1
)

REM numpy est la seule dependance a installer ; on la pose si elle manque
%PY% -c "import numpy" >nul 2>&1
if errorlevel 1 (
    echo Installation de numpy ^(une seule fois^)...
    %PY% -m pip install --quiet numpy
    if errorlevel 1 (
        echo L'installation a echoue. Essayez :  %PY% -m pip install numpy
        pause
        exit /b 1
    )
)

echo Demarrage du studio - laissez cette fenetre ouverte.
echo Pour arreter : Ctrl-C, ou fermez cette fenetre.
echo.
%PY% tools\studio.py

echo.
echo Le studio s'est arrete.
pause
