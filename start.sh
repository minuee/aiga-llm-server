#!/bin/bash

# 로컬 환경에서 직접 서버를 실행하고 로그를 남기는 스크립트

echo "로컬 서버를 시작합니다. nohup을 사용하여 백그라운드에서 시작합니다."

# 가상환경 경로
VENV_PATH=".venv"
VENV_ACTIVATE="$VENV_PATH/bin/activate"

# 가상환경 활성화
if [ ! -f "$VENV_ACTIVATE" ]; then
    echo "❌ 가상환경 없음: $VENV_ACTIVATE"
    exit 1
fi
source "$VENV_ACTIVATE"

# 로그 디렉토리 생성
mkdir -p ./logs/aiga_llm_server

# 프로세스 이름 (중복 실행 방지용)
PROCESS_NAME="uvicorn app.main:app"

# 이전 프로세스 종료
echo "이전 프로세스 확인 및 종료 중..."
PIDS=$(pgrep -f "$PROCESS_NAME")
if [ ! -z "$PIDS" ]; then
    echo "기존 프로세스 발견: $PIDS"
    kill -TERM $PIDS
    sleep 2
    
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

# 새 프로세스 시작 (로컬 실행용 설정, 포트 8001)
echo "새로운 서버 프로세스 시작 중..."
LOG_FILE="./logs/aiga_llm_server/app_$(date +%Y-%m-%d).log"
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level info > "$LOG_FILE" 2>&1 &

# 프로세스 ID 저장
SERVER_PID=$!
echo "서버 프로세스 ID: $SERVER_PID"
echo ""

# 프로세스 시작 확인
sleep 3
if ps -p $SERVER_PID > /dev/null; then
    echo "✅ 서버가 성공적으로 시작되었습니다!"
    echo "프로세스 ID: $SERVER_PID"
    echo "로그 파일: $LOG_FILE"
    echo "서버 URL: http://0.0.0.0:8001"
    echo ""
    echo "로그 확인 명령어:"
    echo "  tail -f $LOG_FILE"
    echo ""
    echo "프로세스 종료 명령어:"
    echo "  kill $SERVER_PID"
    echo "  또는 ./stop.sh"
else
    echo "❌ 서버 시작 실패!"
    echo "로그 파일을 확인해주세요: $LOG_FILE"
    exit 1
fi