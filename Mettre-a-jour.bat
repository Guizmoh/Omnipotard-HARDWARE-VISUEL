@echo off
REM Met a jour le studio : recupere la derniere version depuis GitHub.
REM Rien a installer, pas besoin de git : le depot est public, on telecharge
REM l'archive et on remplace le code. Le dossier out (vos morceaux, vos fonds
REM et vos videos) et le dossier bin (ffmpeg) ne sont jamais touches.
cd /d "%~dp0"
title Mise a jour du studio Omnipotard

echo.
echo   Mise a jour du studio Omnipotard.
echo   IMPORTANT : fermez d'abord la fenetre noire du studio.
echo   Mettre a jour pendant qu'il tourne le laisse sur l'ancienne version,
echo   et le rendu s'arrete en chemin.
echo.
REM Ce fichier doit etre DANS le dossier du projet, a cote de
REM Lancer-le-studio.bat. Lance ailleurs, il y deverserait tout le projet.
if not exist "%~dp0tools\omnipotard_intro.py" (
    echo.
    echo   Ce fichier doit etre place DANS le dossier du projet,
    echo   a cote de Lancer-le-studio.bat, puis double-clique de la.
    echo.
    echo   Dossier actuel : %~dp0
    echo.
    pause
    exit /b 1
)

echo   Version actuellement installee :
findstr /b "VERSION = " tools\omnipotard_intro.py
echo.
pause

set "ZIPURL=https://github.com/Guizmoh/Omnipotard-HARDWARE-VISUEL/archive/refs/heads/main.zip"
set "RACINE=Omnipotard-HARDWARE-VISUEL-main"

echo   Telechargement...
REM Mettre-a-jour.bat ne se remplace pas lui-meme : cmd.exe relit le fichier
REM en cours d execution ligne par ligne, et le reecrire sous ses pieds le
REM ferait derailler. Ce script-ci est court et stable ; tout le reste passe.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'omni-maj.zip'; $d=Join-Path $env:TEMP 'omni-maj'; Invoke-WebRequest -UseBasicParsing '%ZIPURL%' -OutFile $z; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d -Force; $s=Join-Path $d '%RACINE%'; if(-not (Test-Path $s)){throw 'archive inattendue'}; $t=Join-Path '%~dp0' 'tools'; [void](New-Item -ItemType Directory -Force -Path $t); Copy-Item (Join-Path $s 'tools\*') $t -Recurse -Force; foreach($p in Get-ChildItem -File $s){ if($p.Name -ne 'Mettre-a-jour.bat'){Copy-Item $p.FullName '%~dp0' -Force} }; Remove-Item $z,$d -Recurse -Force"

if errorlevel 1 (
    echo.
    echo   La mise a jour a echoue. Pas de connexion, ou GitHub injoignable.
    echo   Vous pouvez aussi telecharger le dossier a la main ici :
    echo   https://github.com/Guizmoh/Omnipotard-HARDWARE-VISUEL
    echo.
    pause
    exit /b 1
)

echo.
echo   C est fait. Version maintenant installee :
findstr /b "VERSION = " tools\omnipotard_intro.py
echo.
echo   Vous pouvez relancer Lancer-le-studio.bat.
echo.
pause
