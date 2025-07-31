# AIGA-LLM-SERVER 환경설정과 구동

## 시작 방법

1. ** 파이썬 환경 설정 **
   ```bash
   sudo apt update
   sudo apt install software-properties-common
   sudo add-apt-repository ppa:deadsnakes/ppa
   sudo apt update
   sudo apt install python3.13 python3-env

2. 저장소 복사
   ```bash
   git clone ...

3. 설치
   ```bash
   ./setup.sh
   
   이 과정에서 .venv/ 가상환경에 패키지를를 설치합니다.

4. 실행환경 .env 설정
   ```bash
   [.env.sample 참조]
   Database(mysql) 서버 설정
   OpenAI 서버 설정
   구동에 필요한 기타 값들 설정

5. 서버 구동
   ```bash
   ./start.sh

6. 서버 구동(수동)
   ```bash
   source .venv/bin/activate (Linux)
   .venv\Scripts/activate.bat (Win)
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 --log-level debug
   
   --workers [process갯수]
   각 worker 간의 공유가 필요(해결: Radis권장)

7. 상태 관리
   ```bash
   ./manage_logs.sh [clean|list|tail]
   
8. 실행 중지
   ```bash
   ./stop.sh
   
   
