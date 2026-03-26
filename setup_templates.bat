@echo off
echo Creating directory structure...

mkdir templates\admin 2>nul
mkdir templates\teacher 2>nul
mkdir templates\parent 2>nul
mkdir templates\errors 2>nul
mkdir static\css 2>nul
mkdir static\js 2>nul
mkdir static\img 2>nul

echo Directory structure created successfully!
echo.
echo Now you need to create the following template files:
echo   - templates/admin/login.html
echo   - templates/teacher/login.html
echo   - templates/parent/login.html
echo   - templates/errors/404.html
echo   - templates/errors/500.html
echo.
pause