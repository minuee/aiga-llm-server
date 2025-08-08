#!/bin/bash

# 서버 시작 스크립트
# 사용법: ./start.sh

# 가상환경 경로 설정
VENV_PATH=".venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"

# 로그 디렉토리 생성
mkdir -p ./logs/aiga_llm_server

# 프로세스 이름 (uvicorn 프로세스 찾기용)
PROCESS_NAME="uvicorn app.main:app"

echo "=== FastAPI 서버 시작 스크립트 ==="
echo "로그 디렉토리: ~/logs/aiga_llm_server"
echo "시작 시간: $(date)"
echo ""

# 가상환경 확인 및 활성화
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ 가상환경을 찾을 수 없습니다: $VENV_ACTIVATE"
    echo "가상환경을 먼저 생성해주세요:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

echo "가상환경 확인됨: $VENV_PATH"
echo ""

# 이전 프로세스 종료
echo "이전 프로세스 확인 및 종료 중..."
PIDS=$(pgrep -f "$PROCESS_NAME")
if [ ! -z "$PIDS" ]; then
    echo "기존 프로세스 발견: $PIDS"
    kill -TERM $PIDS
    sleep 2
    
    # 강제 종료 (필요시)
    PIDS=$(pgrep -f "$PROCESS_NAME")
    if [ ! -z "$PIDS" ]; then
        echo "강제 종료 중..."
        kill -KILL $PIDS
        sleep 1
    fi
    echo "기존 프로세스 종료 완료"
else
    echo "실행 중인 프로세스 없음"
fi

echo ""

# 새 프로세스 시작 (가상환경 활성화 후)
echo "새로운 서버 프로세스 시작 중..."
echo "명령어: source $VENV_ACTIVATE && uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --log-level info"
echo ""

# 가상환경을 활성화하고 백그라운드에서 실행 (로그 리다이렉트 제거)
# nohup bash -c "source $VENV_ACTIVATE && uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --log-level info" > /dev/null 2>&1 &
LOG_FILE="./logs/aiga_llm_server/app_$(date +%Y-%m-%d).log"
nohup bash -c "source $VENV_ACTIVATE && uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --log-level info" > "$LOG_FILE" 2>&1 &


# 프로세스 ID 저장
SERVER_PID=$!
echo "서버 프로세스 ID: $SERVER_PID"

# 프로세스 시작 확인
sleep 3
if ps -p $SERVER_PID > /dev/null; then
    echo "✅ 서버가 성공적으로 시작되었습니다!"
    echo "프로세스 ID: $SERVER_PID"
    echo "로그 디렉토리: ~/logs/aiga_llm_server"
    echo "서버 URL: http://0.0.0.0:8001"
    echo ""
    echo "로그 확인 명령어:"
    echo "  tail -f ~/logs/aiga_llm_server/app_$(date +%Y-%m-%d).log"
    echo "  ./manage_logs.sh tail"
    echo ""
    echo "프로세스 종료 명령어:"
    echo "  kill $SERVER_PID"
    echo "  또는 ./stop.sh"
else
    echo "❌ 서버 시작 실패!"
    echo "로그 디렉토리를 확인해주세요: ~/logs/aiga_llm_server"
    echo "가상환경이 제대로 설정되었는지 확인하세요."
    exit 1
fi 