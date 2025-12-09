#!/bin/bash

# This script is intended to be run by pm2.
# It runs the uvicorn server in the foreground.

# 가상환경 경로 설정
VENV_PATH=".venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"

# 가상환경 확인 및 활성화
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ 가상환경을 찾을 수 없습니다: $VENV_ACTIVATE"
    exit 1
fi
source "$VENV_ACTIVATE"

echo "새로운 서버 프로세스 시작 중... pm2"
# pm2가 로그를 관리하므로, 여기에는 리디렉션이 없습니다.
# --log-level info는 uvicorn의 기본 로그를 활성화합니다.
exec env PYTHONUNBUFFERED=1 uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1 --log-level info