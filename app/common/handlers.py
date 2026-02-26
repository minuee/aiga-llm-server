import re
from typing import Optional, Any
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables.config import RunnableConfig

from ..common.logger import logger
from ..tools.language_set import LANGUAGE_GREETINGS, DEFAULT_GREETING
from ..common.emergency_analyzer import detect_emergency_situation_internal
from ..common.location_analyzer import detect_forbidden_recommendation_internal
from ..common.geocoder import get_address_from_coordinates
from app.config import settings
from ..introduce import EMERGENCY_INTRODUCTION # <--- 이 라인 추가
from kiwipiepy import Kiwi # Kiwi 형태소 분석기 임포트 
# Kiwi 형태소 분석기 초기화 (핸들러 모듈에서 한번만)
kiwi = Kiwi()

# Kiwi 형태소 분석기 초기화 (핸들러 모듈에서 한번만)
kiwi = Kiwi()

# 현재 위치 질문 감지를 위한 키워드 (기존 handle_current_location_query 로직에서 추출)
CURRENT_LOCATION_KEYWORDS = {
    "내", "현재", "위치", "어디", "어디야", "있"
}
# 위치 질문 제외 키워드 (병원/의사 검색 등과 혼동 방지)
LOCATION_QUERY_EXCLUDE_KEYWORDS = {
    "병원", "의사", "찾", "가깝"
}

async def classify_and_handle_initial_requests(state: dict, config: RunnableConfig, current_user_message: str, is_first_interaction_in_session: bool, locale: str) -> Optional[dict]:
    """
    초기 사용자 요청을 분류하고 해당 요청에 대한 응답을 반환합니다.
    - 응급 상황 -> 금지된 추천 -> 현재 위치 질문 순으로 우선순위를 가집니다.
    """
    if not current_user_message:
        return None

    # Kiwi 형태소 분석은 한 번만 수행
    # kiwi.analyze()의 결과는 List[List[Token]] 형태이므로, 첫 번째 문장의 토큰 리스트를 가져와야 함.
    # 각 토큰 객체에서 .form (원형), .lemma (기본형), .tag (품사) 등을 사용
    analyzed_tokens = []
    try:
        if kiwi:
            analyzed_tokens = kiwi.analyze(current_user_message)[0][0]
    except Exception as e:
        logger.error(f"Kiwi 형태소 분석 중 오류 발생: {e}", exc_info=True)
        # 오류 발생 시, 분석된 토큰 없이 진행 (fallback)

    # 응답에 사용할 공통 정보 추출
    greeting = LANGUAGE_GREETINGS.get(locale, DEFAULT_GREETING)
    
    # 1. 응급 상황 체크 (가장 높은 우선순위)
    is_emergency = detect_emergency_situation_internal(current_user_message, analyzed_tokens)
    if is_emergency:
        response_text = EMERGENCY_INTRODUCTION
        if is_first_interaction_in_session:
            response_text = f"{greeting}\n\n  {response_text}"
        response = AIMessage(content=response_text)
        logger.info(f"통합 처리 - 응급 상황 감지 및 처리: {response_text}")
        return {
            "messages": [response], 
            "retry": state.get("retry", 0), 
            "valid": True,
            "location_history": state.get('location_history', []),
            "entity_history": state.get('entity_history', [])
        }

    # 2. 금지된 추천 요청 체크
    is_forbidden, forbidden_term = detect_forbidden_recommendation_internal(current_user_message, analyzed_tokens)
    if is_forbidden:
        response_text = f"죄송합니다. 현재 {settings.project_title} 서비스에서는 '{forbidden_term}'에 대한 추천을 제공하고 있지 않습니다. 추천비대상은 치과와 한의원입니다."
        if is_first_interaction_in_session:
            response_text = f"{greeting}\n\n  {response_text}"
        response = AIMessage(content=response_text)
        logger.info(f"통합 처리 - 금지된 추천 요청 사전 차단: {response_text}")
        return {
            "messages": [response], 
            "retry": state.get("retry", 0), 
            "valid": True,
            "location_history": state.get('location_history', []),
            "entity_history": state.get('entity_history', [])
        }

    # 3. 현재 위치 질문 체크
    is_get_current_location_query = False
            
    if is_get_current_location_query:
        latitude = state.get("latitude")
        longitude = state.get("longitude")

        if latitude is not None and longitude is not None:
            address = await get_address_from_coordinates(latitude, longitude)
            if address:
                logger.info(f"Original address from Nominatim: '{address}'")
                address_parts = [part.strip() for part in address.split(',')]
                if '대한민국' in address_parts:
                    address_parts.remove('대한민국')
                korean_address = ", ".join(address_parts[::-1])
                response_text = f"현재 계신 곳은 '{korean_address}' 입니다."
            else:
                response_text = f"불분명한 좌표 (위도: {latitude}, 경도: {longitude})로 주소를 찾을 수 없었습니다."
            
            if is_first_interaction_in_session:
                response_text = f"{greeting}\n\n {response_text}"

            response = AIMessage(content=response_text)
            logger.info(f"통합 처리 - 현재 위치 질문 처리 완료: {response_text}")
            return {
                "messages": [response], 
                "retry": state.get("retry", 0), 
                "valid": True, 
                "location_history": state.get('location_history', []),
                "entity_history": state.get('entity_history', [])
            }
        else:
            response_text = "죄송합니다. 현재 위치 정보가 없어 정확한 주소를 알려드릴 수 없습니다. 위치 정보 제공에 동의해주시면 더욱 정확한 정보를 드릴 수 있습니다."
            if is_first_interaction_in_session:
                response_text = f"{greeting}\n\n {response_text}"
            response = AIMessage(content=response_text)
            logger.info(f"통합 처리 - 현재 위치 질문 처리 - 위치 정보 없음: {response_text}")
            return {
                "messages": [response], 
                "retry": state.get("retry", 0), 
                "valid": True, 
                "location_history": state.get('location_history', []),
                "entity_history": state.get('entity_history', [])
            }
    return None
