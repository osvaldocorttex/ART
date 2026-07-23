#!/bin/bash
set -e

APP_DIR="/opt/art"
PORT="22023"
HOST="0.0.0.0"
LOG_FILE="$APP_DIR/streamlit.log"

cd "$APP_DIR"

echo "Parando processos antigos do Streamlit..."
pkill -f "streamlit run app.py" 2>/dev/null || true
sleep 2

echo "Iniciando o aplicativo..."
nohup streamlit run app.py --server.port "$PORT" --server.address "$HOST" > "$LOG_FILE" 2>&1 &

echo "Aplicativo iniciado."
echo "Log: $LOG_FILE"
