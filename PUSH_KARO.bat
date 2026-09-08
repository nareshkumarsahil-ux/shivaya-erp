@echo off
title Shivaya ERP - Git Push (Vercel)
echo ==============================================
echo    SHIVAYA CIRCUIT - PUSH TO VERCEL
echo ==============================================
echo.

cd /d "%~dp0"

echo [1/5] In folder ki files push hongi:
echo       %~dp0
echo.

set "CLONE=C:\Users\Giganics\Downloads\shivaya_push"

echo [2/5] GitHub se fresh copy (clone) ho rahi hai...
if exist "%CLONE%" rmdir /s /q "%CLONE%"
git clone https://github.com/nareshkumarsahil-ux/shivaya-erp.git "%CLONE%"
if errorlevel 1 goto :fail

echo.
echo [3/5] Nayi files copy ho rahi hain...
robocopy "%~dp0" "%CLONE%" /E /XD .git __pycache__ .venv node_modules /XF *.zip *.pyc PUSH_KARO.bat >nul
if errorlevel 8 goto :fail

cd /d "%CLONE%"

echo.
echo [4/5] git add + commit...
git config user.name >nul 2>&1 || git config --global user.name "Naresh Kumar Sahil"
git config user.email >nul 2>&1 || git config --global user.email "nareshkumarsahil-ux@users.noreply.github.com"
git add .
git commit -m "fixed first process LC + edit delete modules + salary system"
echo.

echo [5/5] GitHub par push ho raha hai...
git push
echo.

echo ==============================================
echo   DONE! Push success hua to Vercel 30-60 sec
echo   mein naya deploy kar dega.
echo   Upar koi ERROR dikhe to screenshot bhejo.
echo ==============================================
pause
exit /b 0

:fail
echo.
echo ==============================================
echo   !! PROBLEM AAYI HAI !!
echo   Upar wali line padho ya screenshot bhejo.
echo   (Internet chalu hai? Git install hai?)
echo ==============================================
pause
exit /b 1
