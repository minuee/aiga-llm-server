import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

class CustomFormatter(logging.Formatter):
    """커스텀 포맷터 - Windows 환경 고려"""
    
    # Windows 환경에서 색상 지원 여부 확인
    def __init__(self):
        super().__init__()
        self.is_windows = sys.platform.startswith('win')
        self.supports_color = self._supports_color()
        
        # 색상 코드 정의
        self.grey = "\x1b[38;21m"
        self.blue = "\x1b[34;21m"
        self.yellow = "\x1b[33;21m"
        self.red = "\x1b[31;21m"
        self.bold_red = "\x1b[31;1m"
        self.reset = "\x1b[0m"
        
        self.format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        
        # 색상 지원 여부에 따라 포맷 결정
        if self.supports_color:
            self.FORMATS = {
                logging.DEBUG: self.grey + self.format_str + self.reset,
                logging.INFO: self.blue + self.format_str + self.reset,
                logging.WARNING: self.yellow + self.format_str + self.reset,
                logging.ERROR: self.red + self.format_str + self.reset,
                logging.CRITICAL: self.bold_red + self.format_str + self.reset
            }
        else:
            self.FORMATS = {
                logging.DEBUG: self.format_str,
                logging.INFO: self.format_str,
                logging.WARNING: self.format_str,
                logging.ERROR: self.format_str,
                logging.CRITICAL: self.format_str
            }
    
    def _supports_color(self):
        """Windows 환경에서 색상 지원 여부 확인"""
        if not self.is_windows:
            return True
        
        # Windows 10 이상에서 색상 지원 확인
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except:
            return False
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.format_str)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)

def setup_logger(log_level: str = "INFO"):
    """로거 설정 - 24시간마다 자동 로그 파일 생성"""
    
    # 로그 디렉토리 생성 (Windows 환경 고려)
    try:
        # 기본 위치: 사용자 홈 디렉토리
        log_dir = Path.home() / "logs" / "aiga_llm_server"
        log_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        # 권한 문제가 있으면 현재 작업 디렉토리에 생성
        log_dir = Path.cwd() / "logs" / "aiga_llm_server"
        log_dir.mkdir(parents=True, exist_ok=True)
    
    # 'aiga_llm_server'라는 이름의 전용 로거 생성 (루트 로거와 충돌 방지)
    logger = logging.getLogger("aiga_llm_server")
    logger.setLevel(getattr(logging, log_level.upper()))

    # 상위 로거(루트)로의 이벤트 전파 방지 (중복 로그 출력 방지)
    logger.propagate = False

    # 기존 핸들러 제거
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 콘솔 핸들러 (Windows 환경 고려)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = CustomFormatter()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # 파일 핸들러 (24시간마다 자동 로테이션)
    try:
        # TimedRotatingFileHandler 사용 - 매일 자정에 새 파일 생성
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_dir / "app.log",  # 기본 파일명
            when='midnight',  # 자정에 로테이션
            interval=1,  # 1일마다
            backupCount=30,  # 30일치 보관
            encoding='utf-8',
            delay=False  # 즉시 파일 생성
        )
        
        # 로테이션 시 파일명 포맷 설정
        file_handler.suffix = "%Y-%m-%d"  # 파일명 뒤에 날짜 추가
        file_handler.namer = lambda name: name.replace(".log", "") + ".log"  # 기본 .log 유지
        
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        # 에러 전용 파일 핸들러 (24시간마다 자동 로테이션)
        error_handler = logging.handlers.TimedRotatingFileHandler(
            filename=log_dir / "error.log",  # 기본 파일명
            when='midnight',  # 자정에 로테이션
            interval=1,  # 1일마다
            backupCount=30,  # 30일치 보관
            encoding='utf-8',
            delay=False  # 즉시 파일 생성
        )
        
        # 로테이션 시 파일명 포맷 설정
        error_handler.suffix = "%Y-%m-%d"  # 파일명 뒤에 날짜 추가
        error_handler.namer = lambda name: name.replace(".log", "") + ".log"  # 기본 .log 유지
        
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        logger.addHandler(error_handler)
        
        logger.info(f"Logger initialized with 24-hour rotation. Log directory: {log_dir}")
        
    except (PermissionError, OSError) as e:
        # 파일 생성 실패시 콘솔에만 출력
        logger.warning(f"Log file creation failed: {e}. Console output only.")
    
    return logger

# 전역 로거 인스턴스
logger = setup_logger()