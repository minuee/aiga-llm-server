#!/bin/bash

# 가상환경 경로
VENV_PATH=".venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"

# 로그 디렉토리
mkdir -p /home/ubuntu/workspace/aiga-llm-renual/logs/aiga_llm_server

# 가상환경 활성화
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ 가상환경 없음: $VENV_ACTIVATE"
    exit 1
fi

source "$VENV_ACTIVATE"

echo "=== FastAPI 서버 실행 ==="
echo ""

# 로그 파일 (PM2가 직접 수집 가능하므로 출력만 하면 됨)
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8001 \
  --log-level info
