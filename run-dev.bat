@echo off
rem Chạy app từ mã nguồn trên Windows (lần đầu sẽ tạo môi trường và cài thư viện).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist .venv\Scripts\python.exe (
    echo Dang tao moi truong Python...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Khong tim thay Python 3.10+. Hay cai Python tu python.org roi chay lai.
        pause
        exit /b 1
    )
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
)

.venv\Scripts\python.exe -m dichyk %*
