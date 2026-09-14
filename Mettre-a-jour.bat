@echo off
REM Met a jour le studio : recupere la derniere version depuis GitHub.
REM Rien a installer, pas besoin de git : le depot est public, on telecharge
REM l'archive et on remplace le code. Le dossier out (vos morceaux, vos fonds
REM et vos videos) et le dossier bin (ffmpeg) ne sont jamais touches.
cd /d "%~dp0"
title Mise a jour du studio Omnipotard

echo.
echo   Mise a jour du studio Omnipotard.
echo   Si la fenetre du studio est encore ouverte, fermez-la avant de continuer.
echo.
echo   Version actuellement installee :
findstr /b "VERSION = " tools\omnipotard_intro.py
echo.
pause

set "ZIPURL=https://github.com/Guizmoh/Glitch-visualisateur/archive/refs/heads/claude/omnipotard-intro-video-5bj8zu.zip"
set "RACINE=Glitch-visualisateur-claude-omnipotard-intro-video-5bj8zu"

echo   Telechargement...
REM Mettre-a-jour.bat ne se remplace pas lui-meme : cmd.exe relit le fichier
REM en cours d execution ligne par ligne, et le reecrire sous ses pieds le
REM ferait derailler. Ce script-ci est court et stable ; tout le reste passe.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $z=Join-Path $env:TEMP 'omni-maj.zip'; $d=Join-Path $env:TEMP 'omni-maj'; Invoke-WebRequest -UseBasicParsing '%ZIPURL%' -OutFile $z; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d -Force; $s=Join-Path $d '%RACINE%'; if(-not (Test-Path $s)){throw 'archive inattendue'}; $t=Join-Path '%~dp0' 'tools'; [void](New-Item -ItemType Directory -Force -Path $t); Copy-Item (Join-Path $s 'tools\*') $t -Recurse -Force; foreach($f in @('README.md','requirements.txt','Lancer-le-studio.bat','Lancer-le-studio.command','Mettre-a-jour.command','.gitattributes')){$p=Join-Path $s $f; if(Test-Path $p){Copy-Item $p '%~dp0' -Force}}; Remove-Item $z,$d -Recurse -Force"

if errorlevel 1 (
    echo.
    echo   La mise a jour a echoue. Pas de connexion, ou GitHub injoignable.
    echo   Vous pouvez aussi telecharger le dossier a la main ici :
    echo   https://github.com/Guizmoh/Glitch-visualisateur
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
