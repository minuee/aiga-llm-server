#!/bin/bash

# 서버 종료 스크립트
# 사용법: ./stop.sh

# 프로세스 이름 (uvicorn 프로세스 찾기용)
PROCESS_NAME="uvicorn app.main:app"

echo "=== FastAPI 서버 종료 스크립트 ==="
echo "종료 시간: $(date)"
echo ""

# 실행 중인 프로세스 찾기
PIDS=$(pgrep -f "$PROCESS_NAME")

if [ ! -z "$PIDS" ]; then
    echo "실행 중인 프로세스 발견: $PIDS"
    
    # 정상 종료 시도
    echo "정상 종료 시도 중..."
    kill -TERM $PIDS
    
    # 종료 대기
    for i in {1..10}; do
        sleep 1
        if ! pgrep -f "$PROCESS_NAME" > /dev/null; then
            echo "✅ 서버가 정상적으로 종료되었습니다!"
            exit 0
        fi
        echo "종료 대기 중... ($i/10)"
    done
    
    # 강제 종료
    echo "정상 종료 실패, 강제 종료 중..."
    kill -KILL $PIDS
    sleep 2
    
    if ! pgrep -f "$PROCESS_NAME" > /dev/null; then
        echo "✅ 서버가 강제 종료되었습니다!"
    else
        echo "❌ 서버 종료 실패!"
        exit 1
    fi
else
    echo "실행 중인 서버 프로세스가 없습니다."
fi

echo ""
echo "로그 파일 확인:"
echo "  tail -n 20 ~/logs/app.log" 