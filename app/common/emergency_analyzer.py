import re
from typing import Optional, Any
from kiwipiepy import Kiwi
from ..common.logger import logger

# Kiwi 형태소 분석기 인스턴스 생성 (한 번만 실행)
try:
    kiwi = Kiwi()
    logger.info("Kiwipiepy Kiwi 형태소 분석기 초기화 완료.")
except Exception as e:
    kiwi = None
    logger.error(f"Kiwipiepy Kiwi 형태소 분석기 초기화 실패: {e}", exc_info=True)

# 응급 상황을 탐지하기 위한 키워드 리스트
EMERGENCY_KEYWORDS = ["응급실", "비상상황", "죽을거 같아"]

def detect_emergency_situation_internal(text: str, tokens: Optional[list[Any]] = None) -> bool:
    """
    사용자 질문을 분석하여 응급 상황과 관련된 키워드가 포함되어 있는지 탐지합니다.
    Args:
        text (str): 사용자 질문 원문
        tokens (Optional[list[Any]]): 미리 분석된 형태소 토큰 리스트. 제공되지 않으면 함수 내에서 분석.
    Returns:
        bool: 응급 상황 키워드가 탐지되면 True, 아니면 False
    """
    if not kiwi:
        logger.warning("Kiwi 분석기가 없어 응급 상황 분석을 건너뜁니다.")
        return False

    try:
        if tokens is None:
            analyzed_tokens = kiwi.tokenize(text)
        else:
            analyzed_tokens = tokens
        
        for token in analyzed_tokens:
            # 토큰의 원형(lemma) 또는 형태(form)가 응급 키워드에 포함되는지 확인
            if token.lemma in EMERGENCY_KEYWORDS or token.form in EMERGENCY_KEYWORDS:
                logger.info(f"응급 상황 키워드 감지: '{token.form}' (원문: '{text}')")
                return True
        
        # 추가적으로, 형태소 분석 없이 직접 텍스트에 키워드가 포함되어 있는지 확인 (오탈자 등 고려)
        for keyword in EMERGENCY_KEYWORDS:
            if keyword in text:
                logger.info(f"응급 상황 키워드 직접 감지: '{keyword}' (원문: '{text}')")
                return True

    except Exception as e:
        logger.error(f"응급 상황 분석 중 오류 발생: {e}", exc_info=True)

    return False
