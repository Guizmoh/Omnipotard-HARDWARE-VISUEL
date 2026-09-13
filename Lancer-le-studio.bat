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

REM ---- ffmpeg : indispensable pour lire les morceaux et encoder les videos.
REM On le pose dans le dossier du projet plutot que dans le systeme : pas de
REM droits administrateur, pas de PATH a modifier, rien a desinstaller ensuite.
set "FFDIR=%~dp0bin"
if exist "%FFDIR%\ffmpeg.exe" set "PATH=%FFDIR%;%PATH%"
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Telechargement de ffmpeg ^(une seule fois, environ 110 Mo^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'ffomni.zip'; $d=Join-Path $env:TEMP 'ffomni'; Invoke-WebRequest -UseBasicParsing 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d -Force; $e=@(Get-ChildItem $d -Recurse -Filter ffmpeg.exe)[0]; New-Item -ItemType Directory -Force -Path '%FFDIR%' > $null; Copy-Item $e.FullName '%FFDIR%'; Copy-Item (Join-Path $e.Directory.FullName 'ffprobe.exe') '%FFDIR%'; Remove-Item $z,$d -Recurse -Force"
    if exist "%FFDIR%\ffmpeg.exe" (
        set "PATH=%FFDIR%;%PATH%"
        echo   ffmpeg est en place, dans le sous-dossier bin du projet.
    ) else (
        echo.
        echo   Le telechargement a echoue. Deux solutions :
        echo       winget install Gyan.FFmpeg
        echo   ou telecharger a la main sur https://www.gyan.dev/ffmpeg/builds/
        echo   puis copier ffmpeg.exe et ffprobe.exe dans le sous-dossier bin.
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
