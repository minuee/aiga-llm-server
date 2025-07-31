#!/bin/bash

# 가상환경 설정 스크립트
# 사용법: ./setup.sh

echo "=== AIGA-LLM-SERVER 프로젝트 설정 스크립트 ==="
echo "시작 시간: $(date)"
echo ""

# Python 버전 확인
echo "Python 버전 확인 중..."
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "Python 버전: $PYTHON_VERSION"

if [ $? -ne 0 ]; then
    echo "❌ Python3가 설치되어 있지 않습니다."
    echo "Python3를 먼저 설치해주세요."
    exit 1
fi

# 가상환경 생성
echo ""
echo "가상환경 생성 중..."
if [ -d ".venv" ]; then
    echo "기존 가상환경이 존재합니다. 삭제하고 새로 생성합니다."
    rm -rf .venv
fi

python3 -m venv .venv
if [ $? -ne 0 ]; then
    echo "❌ 가상환경 생성 실패!"
    echo "python3-venv 패키지가 설치되어 있지 않습니다."
    echo "다음 명령어로 설치해주세요:"
    echo "  sudo apt update"
    echo "  sudo apt install python3-venv"
    echo ""
    echo "또는 Python 버전에 맞는 패키지를 설치하세요:"
    echo "  sudo apt install python$PYTHON_VERSION-venv"
    exit 1
fi

echo "✅ 가상환경 생성 완료: .venv"

# 가상환경 활성화
echo ""
echo "가상환경 활성화 중..."
source .venv/bin/activate
if [ $? -ne 0 ]; then
    echo "❌ 가상환경 활성화 실패!"
    exit 1
fi

echo "✅ 가상환경 활성화 완료"

# pip 업그레이드
echo ""
echo "pip 업그레이드 중..."
pip install --upgrade pip

# requirements.txt 확인 및 설치
echo ""
if [ -f "requirements.txt" ]; then
    echo "requirements.txt 발견, 패키지 설치 중..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "❌ 패키지 설치 실패!"
        exit 1
    fi
    echo "✅ 패키지 설치 완료"
else
    echo "⚠️  requirements.txt가 없습니다."
    echo "필요한 패키지를 수동으로 설치해주세요:"
    echo "  pip install fastapi uvicorn langchain-openai langgraph"
fi

echo ""
echo "=== 설정 완료 ==="
echo "가상환경: .venv"
echo "활성화 명령어: source .venv/bin/activate"
echo "서버 시작 명령어: ./start.sh"
echo "서버 종료 명령어: ./stop.sh"
echo ""
echo "다음 단계:"
echo "1. ./start.sh (서버 시작)"
echo "2. tail -f ~/logs/app.log (로그 확인)" 