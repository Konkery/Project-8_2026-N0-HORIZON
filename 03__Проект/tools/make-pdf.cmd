@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    echo [make-pdf] full pipeline: md -^> HTML -^> PDF -^> bookmarks...
    python md_to_pdf.py
    echo.
    echo [make-pdf] done - result path is in the 'AUTO pdf:' line above
    pause
) else (
    python md_to_pdf.py %*
)
