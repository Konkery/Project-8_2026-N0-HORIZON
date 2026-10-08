@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    echo [make-pdf] full pipeline: md -^> HTML -^> PDF -^> bookmarks...
    python md_to_pdf.py
    echo.
    echo [make-pdf] done: 04__???????\pre-contract-negotiations.pdf
    pause
) else (
    python md_to_pdf.py %*
)