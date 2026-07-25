@echo off
title Generateur de cours - Salma
cd /d "%~dp0"

echo ============================================
echo   Generateur d'exercices - Salma
echo ============================================
echo.
echo Demarrage du serveur local...
start "Serveur Generation" /min python server.py
echo Serveur demarre (port 8765).
echo.
echo Ouverture de l'application...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765/"
echo.
echo L'application est ouverte dans votre navigateur (connexion requise).
echo Les autres postes du reseau local peuvent s'y connecter via l'adresse IP de ce PC, meme port.
echo Laissez cette fenetre ouverte pendant votre travail.
echo Fermez-la ou appuyez sur Ctrl+C pour arreter le serveur.
echo.
echo ============================================
pause
