#!/bin/bash

# 로그 관리 스크립트
# 사용법: ./manage_logs.sh [clean|list|tail]

LOG_DIR=/Users/kormedi/Documents/WorkPlace/bitbucket/aiga-llm-server/logs/aiga_llm_server

case "$1" in
    "clean")
        echo "30일 이상 된 로그 파일 삭제 중..."
        find $LOG_DIR -name "*.log.*" -mtime +30 -delete
        echo "완료"
        ;;
    "list")
        echo "로그 파일 목록:"
        ls -la $LOG_DIR/*.log* 2>/dev/null || echo "로그 파일이 없습니다."
        ;;
    "tail")
        echo "최신 로그 파일 실시간 모니터링:"
        # TimedRotatingFileHandler는 app.log.2024-01-15 형태로 파일 생성
        latest_log=$(ls -t $LOG_DIR/app.log* 2>/dev/null | head -1)
        if [ -n "$latest_log" ]; then
            tail -f "$latest_log"
        else
            echo "로그 파일이 없습니다."
        fi
        ;;
    *)
        echo "사용법: $0 [clean|list|tail]"
        echo "  clean: 30일 이상 된 로그 파일 삭제"
        echo "  list: 로그 파일 목록 출력"
        echo "  tail: 최신 로그 파일 실시간 모니터링"
        ;;
esac 