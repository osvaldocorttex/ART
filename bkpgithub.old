
@echo off

REM ===== DATA/HORA =====
set DATA=%date:~6,4%-%date:~3,2%-%date:~0,2%
set HORA=%time:~0,2%-%time:~3,2%-%time:~6,2%

set LOG_DIR=C:\ART.TRANSP\logs
set LOG_FILE=%LOG_DIR%\deploy_%DATA%_%HORA%.log

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo =============================== >> %LOG_FILE%
echo 🚀 DEPLOY INICIADO %DATA% %time% >> %LOG_FILE%
echo =============================== >> %LOG_FILE%

echo Iniciando deploy...

REM ===== CONFIG =====
set LOCAL_DIR=/c/ART.TRANSP/
set REMOTE_USER=root
set REMOTE_HOST=143.95.217.226
set REMOTE_PORT=22022
set REMOTE_DIR=/opt/art.transp

echo 📂 Enviando arquivos... >> %LOG_FILE%

"C:\Program Files\Git\bin\bash.exe" -c ^
"rsync -avz --delete --rsync-path='/usr/bin/rsync' -e 'ssh -p %REMOTE_PORT%' ^
--exclude '__pycache__' ^
--exclude '*.pyc' ^
--exclude '*.log' ^
--exclude 'controle_viagens.db' ^
--exclude 'uploads' ^
%LOCAL_DIR% %REMOTE_USER%@%REMOTE_HOST%:%REMOTE_DIR%" >> %LOG_FILE% 2>&1

if %errorlevel% neq 0 (
    echo ❌ ERRO NO DEPLOY! >> %LOG_FILE%
    echo Erro no deploy - veja log: %LOG_FILE%
    pause
    exit /b
)

echo 🔄 Reiniciando sistema... >> %LOG_FILE%

ssh -p %REMOTE_PORT% %REMOTE_USER%@%REMOTE_HOST% "/usr/bin/pkill -f streamlit" >> %LOG_FILE% 2>&1

ssh -p %REMOTE_PORT% %REMOTE_USER%@%REMOTE_HOST% "/usr/bin/nohup /opt/art.transp/venv/bin/streamlit run /opt/art.transp/app.py --server.port 8501 --server.address 0.0.0.0 > /dev/null 2>&1 &" >> %LOG_FILE% 2>&1

echo ✅ DEPLOY FINALIZADO >> %LOG_FILE%
echo =============================== >> %LOG_FILE%

echo.
echo ✅ Deploy finalizado!
echo 📄 Log:
echo %LOG_FILE%

pause