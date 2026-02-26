# app/common/location_analyzer.py

import json
import re
from typing import Optional, Any
from kiwipiepy import Kiwi
from langchain_openai import AzureChatOpenAI
from ..common.logger import logger
from ..tools.location_dic import GROUP_LOCATION_EAMBIUS_RULES, LOCATION_NORMALIZATION_RULES, GROUP_LOCATION_EXPANSION_RULES

# 더 유연한 병원 이름 패턴 (e.g., 강릉아산병원, 서울대병원)
# '병원' 앞에 두 글자 이상의 한글이 오는 경우를 병원 이름으로 간주
hospital_pattern = re.compile(r'[가-힣]{2,}(?:대학교|대학|대)?병원')


# Kiwi 형태소 분석기 인스턴스 생성
# 모델 로딩 시 한 번만 실행
try:
    kiwi = Kiwi()
    logger.info("Kiwipiepy Kiwi 형태소 분석기 초기화 완료.")
except Exception as e:
    kiwi = None
    logger.error(f"Kiwipiepy Kiwi 형태소 분석기 초기화 실패: {e}", exc_info=True)


# 금지된 추천(치과, 한의원 등) 요청을 탐지하기 위한 로직
from ..common.common import DO_NOT_RECOMMNAD_MEDICAL_TYPE

# 추천, 순위, 비교 뉘앙스를 나타내는 단어의 기본형(lemma)
RECOMMENDATION_HINTS = {
    "추천", "좋다", "잘하다", "유명하다", "괜찮다", "어디", "어떻다",
    "비교", "순위", "랭킹", "최고", "전문"
}

def detect_forbidden_recommendation_internal(text: str, tokens: Optional[list[Any]] = None) -> tuple[bool, Optional[str]]:
    """
    사용자 질문을 분석하여 '치과', '한의원' 등에 대한 추천/비교 요청인지 탐지합니다.
    Args:
        text (str): 사용자 질문 원문
        tokens (Optional[list[Any]]): 미리 분석된 형태소 토큰 리스트. 제공되지 않으면 함수 내에서 분석.
    Returns:
        tuple[bool, Optional[str]]: (탐지 여부, 탐지된 금지어)
    """
    if not kiwi:
        logger.warning("Kiwi 분석기가 없어 금지된 추천 분석을 건너뜁니다.")
        return False, None

    try:
        if tokens is None:
            analyzed_tokens = kiwi.tokenize(text)
        else:
            analyzed_tokens = tokens
        
        found_forbidden_term = None
        # 사용자가 입력한 단어 형태 그대로 금지어와 일치하는지 먼저 확인
        for term in DO_NOT_RECOMMNAD_MEDICAL_TYPE:
            if term in text:
                found_forbidden_term = term
                break
        
        # 텍스트에서 직접 일치하는 단어를 못찾았을 경우, 형태소 분석 결과로 재탐색
        if not found_forbidden_term:
            for token in analyzed_tokens:
                if token.form in DO_NOT_RECOMMNAD_MEDICAL_TYPE or token.lemma in DO_NOT_RECOMMNAD_MEDICAL_TYPE:
                    found_forbidden_term = token.form
                    break
        
        if not found_forbidden_term:
            return False, None

        found_recommendation_hint = False
        for token in analyzed_tokens:
            # 토큰의 기본형이 힌트 목록에 있고, 품사가 적절한지 확인
            # VA(형용사), VV(동사), MAG(일반부사), NNG(일반명사)
            if token.lemma in RECOMMENDATION_HINTS and token.tag.startswith(('VA', 'VV', 'MAG', 'NNG')):
                found_recommendation_hint = True
                break

        if found_recommendation_hint:
            logger.info(f"금지된 추천 요청 감지: '{text}'. 금지어: '{found_forbidden_term}', 추천 힌트 발견.")
            return True, found_forbidden_term

    except Exception as e:
        logger.error(f"금지된 추천 분석 중 오류 발생: {e}", exc_info=True)

    return False, None



# 1. 의미 단위 정의
# 거의 변하지 않는 의미의 최소 단위 집합 (lemma 기반)
PROXIMITY_STEMS = {
    "근처", "주변", "가깝다", "인근", "부근", "근방", "옆", "가까이", "가까운데"
}

# 사용자 자신을 지칭하는 대명사/명사 집합
USER_PROXY_NOUNS = {
    "나", "내", "저", "저의", "여기"
}

def classify_location_query(text: str) -> tuple[str, str | None, bool]:
    """
    사용자 질문을 분석하여 위치 검색 유형, 기준 명사, 근접성 여부를 반환합니다.

    Args:
        text (str): 사용자 질문 원문

    Returns:
        tuple[str, str | None, bool]: (분류 결과, 기준 명사, is_nearby)
            - 분류 결과: "USER_LOCATION", "NAMED_LOCATION", "NONE" 중 하나
            - 기준 명사: "NAMED_LOCATION"일 경우 추출된 명사, 그 외에는 None
            - is_nearby: '근처' 등 근접성 관련 단어 포함 여부 (True/False)
    """
    if not kiwi:
        logger.warning("Kiwi 분석기가 없어 위치 분석을 건너뜁니다.")
        return "NONE", None, False

    try:
        # 형태소 및 품사 분석 (기본형, 품사, 원형)
        tokens = kiwi.tokenize(text)
        pos = [(token.lemma, token.tag, token.form) for token in tokens]
        logger.debug(f"형태소 분석 결과: {pos}")

        # 1. '근처' 의미를 가진 단어가 있는지 확인
        has_proximity = any(lemma in PROXIMITY_STEMS for lemma, _, _ in pos)

        # 2. 사용자 지칭어 확인
        has_user_proxy = any(lemma in USER_PROXY_NOUNS for lemma, _, _ in pos)

        # 3. 특정 장소 명사(NNP) 찾기 (사람 이름 제외)
        anchor_noun = None
        
        # 사람 이름과 함께 자주 쓰이는 직책/호칭
        TITLE_NOUNS = {"교수", "의사", "원장", "선생", "박사"}

        potential_anchor_nouns = []
        for i, (lemma, tag, form) in enumerate(pos):
            # 고유명사(NNP)이면서, 사용자 지칭 명사가 아니고, 한 글자가 아닐 경우
            if tag == 'NNP' and lemma not in USER_PROXY_NOUNS and len(lemma) > 1:
                is_person = False
                # 다음 3개 토큰 내에 직책/호칭이 있는지 확인 (이름이 여러 토큰으로 분리되는 경우 대비)
                for j in range(1, 4): # Look ahead up to 3 tokens
                    if i + j < len(pos):
                        next_lemma, next_tag, _ = pos[i+j]
                        if next_lemma in TITLE_NOUNS and next_tag.startswith('NN'):
                            is_person = True
                            break
                
                if not is_person:
                    potential_anchor_nouns.append(lemma)
        
        if potential_anchor_nouns:
            # 여러 장소가 언급될 경우, 가장 마지막에 나온 장소를 선택 (일반적인 문장 구조 고려)
            anchor_noun = potential_anchor_nouns[-1]

        # '여기'는 사용자 위치로 간주
        if '여기' in [lemma for lemma, _, _ in pos]:
             has_user_proxy = True
             if anchor_noun == '여기':
                 anchor_noun = None # '여기' 자체는 기준 명사가 아님

        # 4. 최종 분류
        # 4-1. 명명된 위치(NAMED_LOCATION)가 명확히 있는 경우
        if anchor_noun:
            # 쿼리 의도 파악: NNP 외에 다른 명사(NNG)나 동사(VV) 등이 있는지 확인
            # '병원', '의사' 등 일반 명사 또는 '찾다', '알다' 등 동사
            has_intent_indicator = any(
                tag.startswith('NNG') or tag.startswith('VV')
                for _, tag, _ in pos
            )
            # 만약 NNP만 덩그러니 있다면(예: "경상남도야"), 쿼리가 아닌 답변으로 간주
            if has_intent_indicator:
                logger.info(f"위치 검색 유형: NAMED_LOCATION, 기준 명사: '{anchor_noun}', 근접성: {has_proximity}")
                return "NAMED_LOCATION", anchor_noun, has_proximity

        # 4-2. 명명된 위치는 없지만, '근처' 표현과 사용자 지칭어가 함께 있는 경우 (예: "내 근처")
        if has_proximity and has_user_proxy:
            logger.info("위치 검색 유형: USER_LOCATION (사용자 지칭어 기반 근접 검색)")
            return "USER_LOCATION", None, True

        # 4-3. 명명된 위치나 사용자 지칭어 없이 '근처' 표현만 있는 경우 (예: "가까운 병원")
        if has_proximity and not anchor_noun and not has_user_proxy:
            logger.info("위치 검색 유형: USER_LOCATION (암시적 사용자 위치 기반 근접 검색)")
            return "USER_LOCATION", None, True

        # 4-4. 위 모든 조건에 해당하지 않으면 위치 쿼리가 아님
        logger.debug("분류 가능한 위치 쿼리 유형을 찾지 못했습니다.")
        return "NONE", None, False

    except Exception as e:
        logger.error(f"위치 쿼리 분석 중 오류 발생: {e}", exc_info=True)
        return "NONE", None, False

# 장소 관련 명사 집합
PLACE_NOUNS = {"곳", "지역", "동네", "도시", "장소", "데"}

def analyze_other_location_request(text: str) -> bool:
    """
    사용자가 '다른' 장소를 검색해달라고 '요청'하는지 분석합니다.

    Args:
        text (str): 사용자 질문 원문

    Returns:
        bool: 모호한 '다른 장소' 검색 요청이 맞으면 True, 아니면 False
    """
    if not kiwi:
        logger.warning("Kiwi 분석기가 없어 '다른 장소 요청' 분석을 건너뜁니다.")
        return False

    try:
        tokens = kiwi.tokenize(text)
        pos = [(token.lemma, token.tag, token.form) for token in tokens]
        logger.debug(f"'다른 장소 요청' 분석 - 형태소: {pos}")

        found_other_place = False
        is_request = False

        # 1. '다른' + [장소 명사] 패턴 확인
        for i, (lemma, tag, form) in enumerate(pos):
            # '다른'은 형태가 '다르다'의 활용형(VA+ETM) 또는 관형사(MM)일 수 있음
            is_other = (lemma == '다르다' and tag == 'VA+ETM') or (form == '다른' and tag == 'MM')
            if is_other and i + 1 < len(pos):
                next_lemma, next_tag, _ = pos[i+1]
                # 다음 토큰이 명사이고, 장소 관련 명사 집합에 포함되는지 확인
                if next_tag.startswith('NN') and next_lemma in PLACE_NOUNS:
                    found_other_place = True
                    logger.debug(f"'다른' + 장소 명사 패턴 발견: {form} {next_lemma}")
                    break
        
        if not found_other_place:
            return False

        # 2. 문장이 '요청'이나 '질문'의 형태인지 확인
        last_lemma, last_tag, last_form = pos[-1]

        # 2-1. 물음표로 끝나는 경우
        if last_form == '?':
            is_request = True
            logger.debug("요청/질문 형태 발견: 물음표")

        # 2-2. '...해줘', '...알려줘' 등 요청형 어미로 끝나는 경우
        # 예: ('찾다','VV'), ('아','EC'), ('주다','VX'), ('어','EF')
        if not is_request and len(pos) >= 2:
            # 마지막 토큰이 어미(EF)이고, 그 앞이 보조용언 '주다'(VX)이면 요청으로 간주
            if last_tag == 'EF' and pos[-2][0] == '주다' and pos[-2][1] == 'VX':
                is_request = True
                logger.debug("요청/질문 형태 발견: '...줘' 어미")

        # 2-3. "...어때요?" 와 같은 질문 형식
        if not is_request and (last_form.endswith("요") or last_form.endswith("까")) and last_tag == 'EF':
             is_request = True
             logger.debug("요청/질문 형태 발견: '...요', '...까' 어미")

    except Exception as e:
        logger.error(f"'다른 장소 요청' 분석 중 오류 발생: {e}", exc_info=True)
        return False

async def update_location_context(
    llm: AzureChatOpenAI,
    user_message: str,
    location_history: list,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None
) -> tuple[list, Optional[str]]:
    """
    사용자 메시지를 분석하여 위치 기록을 업데이트하고, 필요한 경우 명확한 설명을 요청하는 질문을 반환합니다.
    """
    
    # 1. 특정 병원 이름이 포함된 쿼리는 위치 컨텍스트 업데이트를 건너뜁니다.
    if hospital_pattern.search(user_message):
        logger.info(f"병원 이름 패턴이 일치하여 위치 컨텍스트 업데이트를 건너뜁니다.")
        return location_history, None

    classification, anchor_noun, is_nearby = classify_location_query(user_message)
    
    # 2. "내 근처" (USER_LOCATION) 쿼리 처리
    if classification == "USER_LOCATION":
        logger.info("위치 분석: 'USER_LOCATION' 쿼리 감지.")
        new_gps_context = {"type": "GPS", "latitude": latitude, "longitude": longitude}
        # GPS 정보가 없으면 기록에 추가하지 않음
        if latitude is None or longitude is None:
            return location_history, None
        # 마지막 기록이 동일한 GPS 정보가 아니면 추가
        if not location_history or location_history[-1] != new_gps_context:
            return location_history + [new_gps_context], None
        else:
            return location_history, None

    # 3. 명명된 위치(NAMED_LOCATION) 처리
    if classification == "NAMED_LOCATION" and anchor_noun:
        logger.info(f"명명된 위치 감지. 기준 명사: '{anchor_noun}', 근접성: {is_nearby}.")

        # '춘천시' -> '춘천'과 같이 일반적인 접미사 '시', '군', '구'를 제거하여 정규화
        if len(anchor_noun) > 2 and anchor_noun.endswith(('시', '군', '구')):
            original_anchor_noun = anchor_noun
            anchor_noun = anchor_noun[:-1]
            logger.info(f"접미사 제거 후 기준 명사 정규화: '{original_anchor_noun}' -> '{anchor_noun}'")

        # 이미 해결된 컨텍스트에 해당 명사가 있는지 확인
        is_resolved = False
        for history_item in reversed(location_history):
            if history_item.get("status") == "resolved":
                if anchor_noun in (history_item.get("sido", ""), history_item.get("sigungu", "")):
                    is_resolved = True
                    logger.info(f"'{anchor_noun}'은(는) 이미 해결된 위치 컨텍스트에 존재합니다.")
                    break
        
        if is_resolved:
            logger.info(f"'{anchor_noun}'은(는) 이미 해결된 위치 컨텍스트이지만, 현재 대화의 최신 위치로 기록을 강제 업데이트합니다.")
            
            # sido 정보는 이전 기록에서 가져옴
            resolved_sido = None
            for history_item in reversed(location_history):
                if history_item.get("status") == "resolved" and anchor_noun in (history_item.get("sigungu", "")):
                    resolved_sido = history_item.get("sido")
                    break

            new_context = {
                "type": "CONTEXTUAL", 
                "sido": resolved_sido, 
                "sigungu": anchor_noun, 
                "status": "resolved", 
                "is_nearby": is_nearby
            }
            return location_history + [new_context], None

        # 새로운 위치 컨텍스트 처리
        # 먼저, 문장 자체에서 명확하게 위치를 특정할 수 있는지 확인 (예: '서울 중구')
        sido_in_message = None
        for sido_long, sido_short in LOCATION_NORMALIZATION_RULES:
            if sido_long in user_message or sido_short in user_message:
                sido_in_message = sido_long
                break
        
        if sido_in_message:
            # '강원도 춘천'과 같은 케이스: 메시지에서 시/도를 찾았으므로 바로 컨텍스트를 확정합니다.
            logger.info(f"시/군/구 '{anchor_noun}'와(과) 시/도 '{sido_in_message}'를 문장에서 함께 발견했습니다. 컨텍스트를 직접 설정합니다.")
            new_context = {"type": "CONTEXTUAL", "sido": sido_in_message, "sigungu": anchor_noun, "status": "resolved", "is_nearby": is_nearby}
            if not location_history or location_history[-1] != new_context:
                return location_history + [new_context], None
            else:
                return location_history, None

        # 문맥으로 해결되지 않은 경우, 모호성 검사 및 LLM 호출 등 진행
        # 3a: 모호한 신규 위치 (예: '광주', '중구')
        if anchor_noun in GROUP_LOCATION_EAMBIUS_RULES:
            logger.info(f"모호한 신규 위치 '{anchor_noun}'. 명확화 질문과 함께 보류 중인 컨텍스트 추가.")
            options = GROUP_LOCATION_EAMBIUS_RULES[anchor_noun]
            question = f"문의하신 '{anchor_noun}'은(는) 어떤 지역을 말씀하시는 건가요? (예: {options})"
            # 나중에 해결하기 위해 보류 중인 항목 추가
            new_context = {"type": "CONTEXTUAL", "sigungu": anchor_noun, "sido": None, "status": "pending_clarification", "is_nearby": is_nearby}
            return location_history + [new_context], question
        # 3b: 모호하지 않은 신규 위치 (하지만 시/도 정보가 필요할 수 있음)
        else:
            # anchor_noun이 시/도 이름인지 먼저 확인
            normalized_sido = None
            for sido_long, sido_short in LOCATION_NORMALIZATION_RULES:
                if anchor_noun == sido_long or anchor_noun == sido_short:
                    normalized_sido = sido_long  # 정식 명칭으로 통일
                    break
            
            if normalized_sido:
                # 시/도 이름이 맞을 경우, 바로 resolved 상태의 컨텍스트 추가
                logger.info(f"시/도 레벨 위치 '{normalized_sido}' 감지. 컨텍스트를 직접 설정합니다.")
                new_context = {"type": "CONTEXTUAL", "sido": normalized_sido, "sigungu": None, "status": "resolved", "is_nearby": is_nearby}
                if not location_history or location_history[-1] != new_context:
                    return location_history + [new_context], None
                else:
                    return location_history, None
            else:
                # 3c: LLM을 이용한 최종 분류 (최후의 수단)
                logger.debug(f"'{anchor_noun}'에 대한 위치 여부를 LLM으로 최종 판단합니다.")
                
                # GROUP_LOCATION_EXPANSION_RULES에 해당하는지 먼저 확인
                if anchor_noun in GROUP_LOCATION_EXPANSION_RULES:
                    logger.info(f"그룹 지역명 '{anchor_noun}' 감지. 컨텍스트를 직접 설정합니다.")
                    # 그룹 지역명은 시/도 또는 시/군/구 개념이 아니므로, 그대로 anchor_noun을 sido 저장
                    new_context = {"type": "CONTEXTUAL", "sido": anchor_noun, "sigungu": None, "status": "resolved", "is_nearby": is_nearby}
                    if not location_history or location_history[-1] != new_context:
                        return location_history + [new_context], None
                    else:
                        return location_history, None

                prompt_rules = "\n\n다음과 같은 그룹 지역명 규칙이 있습니다. 이들은 지리적 위치로 간주되어야 합니다:\n"
                for group_name, explanation in GROUP_LOCATION_EXPANSION_RULES.items():
                    prompt_rules += f"- {group_name}: {explanation}\n"

                prompt = f"""주어진 "문장"의 문맥 안에서 "단어"가 지리적 위치로 사용되었는지 판단해주세요.
대답은 JSON 형식으로 {{"is_location": true}} 또는 {{"is_location": false}} 중 하나로만 반환해주세요.
단 대한민국의 영토내의 지리적 위치만 해당이 됩니다. 너의 판단으로 외국나라이름이거나 지명일경우에는 {{"is_location": false, "is_national":true}}를 결과로 내주세요.
{prompt_rules}

[예시 1]
문장: "무릅이 너무 아파요."
단어: "무릅"
대답: {{"is_location": false}}

[예시 2]
문장: "춘천에서 제일 큰 병원은 어디인가요?"
단어: "춘천"
대답: {{"is_location": true}}

[예시 3]
문장: "경상도에 있는 병원 알려줘"
단어: "경상도"
대답: {{"is_location": true}}
---
[실제 작업]
문장: "{user_message}"
단어: "{anchor_noun}"
대답:"""
                try:
                    llm_response = await llm.ainvoke(prompt)
                    response_data = json.loads(llm_response.content.strip())
                    is_location = response_data.get("is_location", False)
                    is_national = response_data.get("is_national", False) # is_national 값 추가 확인

                    logger.info(f"LLM 판단 결과: '{anchor_noun}'은(는) 위치가 {'맞습니다' if is_location else '아닙니다'}. 해외 여부: {is_national}")

                    if is_national: # is_national이 True인 경우 (해외 지명으로 판단한 경우)
                        # question = f"문의하신 '{anchor_noun}'은(는) 대한민국 내 영토에 해당 되지 않는 지역으로 판단됩니다. AIGA는 대한민국 내 병원과 의사 정보를 대상으로 하고 있습니다."
                        logger.info(f"LLM이 '{anchor_noun}'을(를) 해외 지명으로 판단하여 위치 컨텍스트 업데이트를 건너뜁니다.")
                        return location_history, None
                    
                    if is_location:
                        # LLM이 국내 지명으로 판단한 경우 (is_national이 False 또는 없거나 is_location이 True)
                        logger.info(f"LLM이 위치로 판단한 '{anchor_noun}'. 시/도 요청.")
                        question = f"문의하신 '{anchor_noun}'은(는) 어느 시/도에 속한 지역을 말씀하시는 건가요?"
                        new_context = {"type": "CONTEXTUAL", "sigungu": anchor_noun, "sido": None, "status": "pending_clarification", "is_nearby": is_nearby}
                        return location_history + [new_context], question
                    else:
                        # LLM이 국내 지명이 아니라고 판단한 경우 (is_national이 False 또는 없거나 is_location이 False)
                        # is_national이 True인 경우는 위에서 이미 처리되었으므로, 이 else는 is_location이 False일 때의 국내 지명 아님 케이스
                        return location_history, None
                except Exception as e:
                    logger.error(f"LLM 기반 위치 판단 중 오류 발생: {e}", exc_info=True)
                    # 오류 발생 시 안전하게 컨텍스트 변경 없이 종료
                    return location_history, None



    # 4. 명확화 질문에 대한 사용자 답변 처리
    pending_context_index = -1
    if location_history:
        # history를 역순으로 순회하며 가장 최근의 pending_clarification 항목을 찾음
        for i, hist in reversed(list(enumerate(location_history))):
            if hist.get("status") == "pending_clarification":
                pending_context_index = i
                break
    
    # 보류 중인 컨텍스트가 있고, 현재 메시지가 새로운 위치 쿼리가 아닐 때
    if pending_context_index != -1:
        sido_in_message = None
        # 사용자 답변에서 시/도 이름 추출
        for sido_long, sido_short in LOCATION_NORMALIZATION_RULES:
            # 메시지에 시/도 전체 이름 또는 축약명이 포함되어 있는지 확인
            if sido_long in user_message or sido_short in user_message:
                sido_in_message = sido_long # 정식 명칭으로 저장
                break
        
        if sido_in_message:
            logger.info(f"명확화 답변 '{sido_in_message}' 수신. 보류 중인 위치 컨텍스트 해결 중.")
            # 찾은 보류 컨텍스트를 업데이트
            updated_history = list(location_history)
            updated_history[pending_context_index]["sido"] = sido_in_message
            updated_history[pending_context_index]["status"] = "resolved"
            
            # (선택) 다른 모든 pending_clarification 항목을 제거하여 상태를 깨끗하게 유지
            clean_history = [
                hist for i, hist in enumerate(updated_history)
                if hist.get("status") != "pending_clarification" or i == pending_context_index
            ]
            
            return clean_history, None

    # 5. 위치 관련 규칙이 실행되지 않은 경우
    logger.info("실행 가능한 위치 기반 규칙이 없습니다.")
    return location_history, None


def check_location_info(current_message: str) -> dict:
    """
    Checks the current message for top-level region info and returns a flag dictionary.
    """
    logger.info(f"check_location_info: Analyzing message '{current_message}' for top-level regions.")
    
    sido_names = {name for rule in LOCATION_NORMALIZATION_RULES for name in rule}
    group_names = set(GROUP_LOCATION_EXPANSION_RULES.keys())
    valid_regions = sido_names.union(group_names)

    # Check if any valid region name is present as a substring in the message.
    # This is more robust for handling Korean postpositions (e.g., "울산에서").
    found_regions = [region for region in valid_regions if region in current_message]
    
    flag = {
        'has_location': False,
        'is_ambiguous': False,
        'term': None
    }

    if found_regions:
        flag['has_location'] = True
        # If multiple are found (e.g., "서울 경기"), pick the longest name.
        flag['term'] = max(found_regions, key=len)
        flag['is_ambiguous'] = False
        
    logger.info(f"check_location_info: Generated flag {flag}")
    return flag

async def analyze_proximity_with_llm(llm: AzureChatOpenAI, user_message: str) -> bool:
    """LLM을 사용하여 사용자 메시지가 '근처' 검색인지 '지역 내' 검색인지 판단합니다."""
    if not user_message:
        return False
    
    prompt = f"""You are a text analysis assistant. Your task is to determine if the user's request is a proximity-based search (near, around) or a containment-based search (in, at).

- If the request is about proximity (e.g., "near Gangnam station", "around my location", "close to the city hall"), respond with only the word: true
- If the request is about containment (e.g., "hospitals in Gangnam", "dentists in Seoul"), respond with only the word: false
- If there is no location information or it's unclear, assume it's a containment search and respond with: false

User request: "{user_message}"

Respond with only "true" or "false".."""

    try:
        response = await llm.ainvoke(prompt)
        logger.debug(f"Proximity analysis with LLM. Query: '{user_message}', Raw Response: '{response.content}'")
        result = response.content.strip().lower()
        return result == "true"
    except Exception as e:
        logger.error(f"Proximity analysis with LLM failed: {e}", exc_info=True)
        return False # 에러 발생 시 안전하게 False로 처리
