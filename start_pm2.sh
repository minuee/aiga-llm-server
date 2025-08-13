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

# uvicorn 실행 (포그라운드)
# --reload 옵션은 start_old.sh에 있었지만, 프로덕션 환경에서는 빼는 것이 좋을 수 있습니다.
# 우선은 기존 스크립트와 동일하게 유지합니다.

exec env PYTHONUNBUFFERED=1 uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level info
