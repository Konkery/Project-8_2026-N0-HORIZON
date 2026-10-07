@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    echo [make-pdf] авторежим: поиск отчета и вшивка закладок...
    python pdf_add_bookmarks.py
    echo.
    echo [make-pdf] готово. Закройте PDF-ридер перед следующим прогоном, если файл занят.
    pause
) else (
    python pdf_add_bookmarks.py %*
)
