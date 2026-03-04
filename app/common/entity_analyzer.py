# app/common/entity_analyzer.py
import json
import re
from ..common.logger import logger
from typing import List, Dict, Any, Optional

from langchain_core.messages import BaseMessage, ToolMessage, HumanMessage
from langchain_openai import AzureChatOpenAI

# 🚨 추가: location_dic에서 SIDO_NAMES, GROUP_NAMES 임포트
from ..tools.location_dic import SIDO_NAMES, GROUP_NAMES

def _add_unique_items(target_list: List[str], items_to_add: Optional[Any]):
    """
    Helper function to add string items to a list while ensuring uniqueness.
    Filters out None or empty strings.
    """
    if not items_to_add:
        return
    
    if not isinstance(items_to_add, list):
        items_to_add = [items_to_add]

    for item in items_to_add:
        if isinstance(item, str) and item and item not in target_list:
            target_list.append(item)

def _add_unique_doctors(target_list: List[Dict[str, Any]], doctors_to_add: List[Dict[str, Any]]):
    """
    Helper to add doctor objects to a list, ensuring uniqueness based on (name, hospital) tuple.
    """
    existing_doctors = {(doc.get('name'), doc.get('hospital')) for doc in target_list}

    for doc in doctors_to_add:
        name = doc.get('name')
        hospital = doc.get('hospital')
        
        if name and (name, hospital) not in existing_doctors:
            target_list.append(doc)
            existing_doctors.add((name, hospital))


async def _extract_entities_from_text(llm: AzureChatOpenAI, text: str) -> Dict[str, List[str]]:
    """
    Uses a lightweight LLM call to extract key medical entities from raw user text.
    """
    # logger.info(f"Attempting to extract entities from user text: '{text}'")
    
    prompt = f"""사용자의 문장에서 '질병', '진료과', '병원', '의사', '지역' 이름을 추출하세요.
- disease: 질병 또는 증상의 이름.
- department: 진료과의 이름.
- hospital: 병원의 이름.
- doctor: 의사로 추정되는 사람의 이름.
- location: 지역의 이름 (예: '서울', '강남구'). **병원 이름에 포함된 지역명(예: '서울대병원'의 '서울')은 'location'으로 분류하지 마세요. 'location'은 오직 독립적인 지역 명칭일 때만 추출해야 합니다.**
반드시 아래 예시와 같이, 추출된 이름을 문자열 리스트로 포함하는 JSON 객체 형식으로만 응답해야 합니다.
[예시 1]
Sentence: "간암 명의 추천해줘"
JSON: {{"diseases": ["간암"], "departments": [], "hospitals": [], "doctors": [], "location": null}}
[예시 2]
Sentence: "허리 디스크로 고생중인데, 서울 우리들병원 김철수 의사 어때?"
JSON: {{"diseases": ["허리 디스크"], "departments": [], "hospitals": ["우리들병원"], "doctors": ["김철수"], "location": "서울"}}
[예시 3]
Sentence: "서울대병원에서 치료받고 싶어요. 심장내과 선생님을 추천해 주세요."
JSON: {{"diseases": [], "departments": ["심장내과"], "hospitals": ["서울대병원"], "doctors": [], "location": null}}
[예시 4]
Sentence: "서울에서 서울대병원 심장내과 선생님을 추천해 주세요."
JSON: {{"diseases": [], "departments": ["심장내과"], "hospitals": ["서울대병원"], "doctors": [], "location": "서울"}}
[실제 작업]
Sentence: "{text}"
JSON:"""

    try:
        response = await llm.ainvoke(prompt)
        cleaned_json_str = re.sub(r'```json\s*|\s*```', '', response.content.strip())
        entities = json.loads(cleaned_json_str)
        
        # location은 리스트가 아니므로 별도로 처리
        extracted_location = None
        if "location" in entities:
            if isinstance(entities["location"], list) and len(entities["location"]) > 0:
                extracted_location = entities["location"][0]
            elif isinstance(entities["location"], str) and entities["location"] not in ["None", "null", ""]:
                extracted_location = entities["location"]

        for key in ["diseases", "departments", "hospitals", "doctors"]:
            if key not in entities or not isinstance(entities[key], list):
                entities[key] = []

        entities["location"] = extracted_location # 추출된 location을 최종 할당


        # logger.info(f"LLM extracted entities from text: {entities}")
        return entities
    except Exception as e:
        # logger.error(f"Failed to extract entities from text with LLM: {e}", exc_info=True)
        return {"diseases": [], "departments": [], "hospitals": [], "doctors": [], "location": None}


async def update_entity_context(
    llm: AzureChatOpenAI,
    messages: List[BaseMessage]
) -> Optional[Dict[str, List[Any]]]: # Return type changed to Optional[Dict[str, List[Any]]]
    """
    Analyzes user queries and tool results to extract key medical entities from the current turn.
    It does not manage the history; it only extracts entities for the current turn.
    """
    
    logger.info("Starting entity extraction for current turn...")
    
    current_context = {"hospitals": [], "doctors": [], "departments": [], "diseases": [], "location": None}

    last_human_message: Optional[HumanMessage] = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human_message = msg
            break
    
    tool_messages_for_turn = []
    if last_human_message:
        start_index = messages.index(last_human_message)
        for i in range(start_index + 1, len(messages)):
            msg = messages[i]
            if isinstance(msg, ToolMessage):
                tool_messages_for_turn.append(msg)
            elif isinstance(msg, HumanMessage): 
                break

    if last_human_message:
        logger.debug(f"Analyzing HumanMessage for entities: '{last_human_message.content}'")
        extracted_from_human = await _extract_entities_from_text(llm, last_human_message.content)
        _add_unique_items(current_context["diseases"], extracted_from_human.get("diseases"))
        _add_unique_items(current_context["departments"], extracted_from_human.get("departments"))
        _add_unique_items(current_context["hospitals"], extracted_from_human.get("hospitals"))
        current_context["location"] = extracted_from_human.get("location") # location 추가
        
        human_doctors_as_obj = [{"name": name, "hospital": None, "department": None} for name in extracted_from_human.get("doctors", [])]
        _add_unique_doctors(current_context["doctors"], human_doctors_as_obj)

    if tool_messages_for_turn:
        logger.info(f"Analyzing {len(tool_messages_for_turn)} ToolMessage(s) for entities...")
        for tool_message in tool_messages_for_turn:
            try:
                content = json.loads(tool_message.content)
                
                if content.get("migrated") is True:
                    logger.debug("ToolMessage is a migrated placeholder. Skipping entity extraction from it.")
                    continue

                answer = content.get("answer")
                if isinstance(answer, dict):
                    if answer.get("doctors") and isinstance(answer.get("doctors"), list):
                        docs = answer["doctors"]
                        tool_doctors_as_obj = [
                            {
                                "name": d.get("name"),
                                "hospital": d.get("hospital_name") or d.get("hospital"),
                                "department": d.get("deptname")
                            } for d in docs
                        ]
                        _add_unique_doctors(current_context["doctors"], tool_doctors_as_obj)

                        hospitals_from_docs = [d.get("hospital") for d in tool_doctors_as_obj if d.get("hospital")]
                        depts_from_docs = [d.get("department") for d in tool_doctors_as_obj if d.get("department")]
                        _add_unique_items(current_context["hospitals"], hospitals_from_docs)
                        _add_unique_items(current_context["departments"], depts_from_docs)

                    if answer.get("hospitals") and isinstance(answer.get("hospitals"), list):
                        hosps = answer["hospitals"]
                        _add_unique_items(current_context["hospitals"], [h.get("name") for h in hosps])
                        depts = [h.get("department") for h in hosps]
                        flat_depts = [item for sublist in depts if sublist for item in (sublist if isinstance(sublist, list) else [sublist])]
                        _add_unique_items(current_context["departments"], flat_depts)

                    _add_unique_items(current_context["diseases"], answer.get("disease"))
                    _add_unique_items(current_context["departments"], answer.get("department"))
                    _add_unique_items(current_context["hospitals"], answer.get("hospital"))
                    logger.info(f"Entities extracted from ToolMessage answer.")

            except (json.JSONDecodeError, TypeError):
                logger.warning("Could not parse ToolMessage content for entity extraction.")
            except Exception as e:
                logger.error(f"Unexpected error during ToolMessage entity extraction: {e}", exc_info=True)

    logger.info(f"Current turn entity snapshot: {current_context}")

    # Return current_context if it contains any extracted entities, otherwise None
    if any(current_context.values()):
        return current_context
    return None


async def extract_entities_for_routing(llm: AzureChatOpenAI, state: dict) -> dict:
    """
    Uses a fast LLM call to extract key entities for routing to a simpler tool, 
    considering the whole conversation context and pre-extracted entities,
    and also leveraging the persistent entity_history.
    """

    logger.info("Starting entity extraction for routing, considering full conversation history and entity_history.")

    # 1. 먼저 update_entity_context를 호출하여 현재 턴의 기본적인 엔티티 정보를 추출합니다.
    pre_extracted_entities = await update_entity_context(llm, state['messages'])
    

    # 초기 엔티티 설정
    current_extracted = {
        "diseases": [],
        "departments": [],
        "hospitals": [],
        "doctors": [],
        "location": None # location 필드 추가
    }

    if pre_extracted_entities: # pre_extracted_entities가 None이 아닐 경우에만 처리
        _add_unique_items(current_extracted["diseases"], pre_extracted_entities.get("diseases"))
        _add_unique_items(current_extracted["departments"], pre_extracted_entities.get("departments"))
        _add_unique_items(current_extracted["hospitals"], pre_extracted_entities.get("hospitals"))
        _add_unique_doctors(current_extracted["doctors"], pre_extracted_entities.get("doctors"))

    
    # 대화 기록을 LLM 프롬프트에 포함하기 위해 포맷팅
    def format_message_for_history(msg):
        if isinstance(msg, ToolMessage):
            try:
                # ToolMessage의 content가 JSON이면 answer 필드만 추출하여 포함
                content_json = json.loads(msg.content)
                if content_json.get("migrated") is True:
                     return "ToolMessage: [Previous tool result (migrated)]"
                elif content_json.get("answer"):
                    answer_content = content_json.get('answer')
                    if isinstance(answer_content, dict):
                        answer_to_send = answer_content.copy()
                        # Remove address-related keys to prevent LLM from misinterpreting location
                        for key in ['address', 'hospital_address', 'location']:
                            if key in answer_to_send:
                                del answer_to_send[key]
                        return f"ToolMessage (answer): {json.dumps(answer_to_send, ensure_ascii=False)}"
                    else:
                        # answer_content가 dict가 아닌 경우 (예: string) 그대로 사용
                        return f"ToolMessage (answer): {json.dumps(answer_content, ensure_ascii=False)}"
                else:
                    return f"ToolMessage: {msg.content}"
            except json.JSONDecodeError:
                return f"ToolMessage: {msg.content}"
        else:
            return f"{type(msg).__name__}: {msg.content}"

    history_messages = [format_message_for_history(msg) for msg in state['messages'][-10:]] # 최근 10개 메시지
    history = "\n".join(history_messages)

    hospital_names = []
    if current_extracted.get('hospitals'):
        for item in current_extracted['hospitals']:
            if isinstance(item, dict) and item.get('name'):
                hospital_names.append(item['name'])
            elif isinstance(item, str):
                hospital_names.append(item)
    
    doctor_names = []
    if current_extracted.get('doctors'):
        for item in current_extracted['doctors']:
            if isinstance(item, dict) and item.get('name'):
                doctor_names.append(item['name'])
            elif isinstance(item, str):
                doctor_names.append(item)

    # LLM 프롬프트에 현재까지 추출된 엔티티 정보를 포함하여 전달
    prompt = f"""아래 대화 기록과 현재까지 추출된 엔티티 정보 (현재 턴 및 persistent entity_history에서 통합됨)를 모두 참고하여 'location', 'disease', 'department', 'target'('의사' 또는 '병원')를 추출합니다.
**현재까지 추출된 엔티티 정보는 아래 JSON 블록으로 제공되며, 이 정보는 사용자의 명시적인 질문 또는 이전 대화의 맥락에서 통합된 가장 신뢰할 수 있는 엔티티 데이터입니다. LLM은 이 정보를 최우선으로 활용하여 다음 단계를 결정해야 합니다.**

[현재까지 추출된 엔티티 (통합 정보)]
{{
    "diseases": {json.dumps(current_extracted['diseases'], ensure_ascii=False)},
    "departments": {json.dumps(current_extracted['departments'], ensure_ascii=False)},
    "hospitals": {json.dumps(hospital_names, ensure_ascii=False)},
    "doctors": {json.dumps(doctor_names, ensure_ascii=False)},
    "location": {json.dumps(current_extracted['location'], ensure_ascii=False)}
}}

[규칙]
1. **그룹 지역명 인식**: "부울경", "수도권", "전라도", "경상도", "충청도"와 같은 광역 그룹 이름은 'location'으로 추출되어야 한다.
2. **매우 중요: '소아과', '내과', '심장내과'와 같은 진료과목 이름은 절대로 'disease' 필드에 넣지 말라. 'disease' 필드는 '감기', '고혈압', '당뇨'와 같은 실제 질병이나 증상 이름만 포함해야 한다. 진료과목은 'department' 필드에만 해당된다.**
   - **만약 'disease'가 명확하게 추출되었다면, 해당 질병을 주로 다루는 진료과목을 유추하여 'department' 필드를 채워 넣어라. 예를 들어, '소아 아토피 피부염'이 disease라면 '피부과'나 '소아청소년과'를 department로 유추할 수 있다.**
   - **만약 질병명만 있고 진료과목 유추가 어렵다면 'department'는 null로 설정한다.**
3. '내 근처', '여기 근처' 등 사용자 자신을 기준으로 하는 단어는 'location'이 아니다. 이런 단어가 보이면 'location'은 null로 설정해라. 최신 순의 대화 흐름을 판단하여 location을 추출해야 한다. 병원정보의 해당하는 address파마리터를 절대 참고하지 말라.
4. 3번의 경우 휴먼메시지가 아닌 AI가 답변한 메시지 내에서 가령 "현재 위치 근처에서" 등의 사용자 자신을 기준으로 하는 단어는 'location'을 null로 설정해라.
5. 대화 기록에서 이미 '시/도' 정보(예: 서울)가 언급되었고, 마지막 질문에 '구/동' 정보(예: 중구)만 있다면, 이 둘을 조합하여 '서울 중구'와 같이 완전한 지역명을 'location'으로 추출해야 한다.
6. **대화의 전체 맥락을 고려하세요. 사용자의 마지막 질문에 특정 정보(예: 진료과)가 없다면, 이전 대화들에서 해당하는 가장 최신 정보를 찾아 사용해야 한다.**
7. ** 어떤 경우에도 'target'은 null이 될 수 없으며, 반드시 '의사' 또는 '병원' 중 하나를 선택해야 한다.**
   - **대화 기록을 참고하여 설정한다.
[예시]
대화 기록:
HumanMessage: 소아 아토피 피부염 전문의 추천

JSON:
{{
  "location": null,
  "disease": "소아 아토피 피부염",
  "department": ["피부과", "소아청소년과"],
  "target": "의사",
  "target_reason" : "전문의를 추천해달라고 요청을 해사"
}}

대화 기록:
HumanMessage: 기침이 심한데, 내 근처 병원 알려줘

JSON:
{{
  "location": null,
  "disease": "기침",
  "department": ["호흡기내과"],
  "target": "병원",
  "target_reason" : "근처 병원을 알려해달라고 요청을 해사"
}}

[대화 기록]
{history}

JSON 객체 형식으로만 응답하세요. 값이 없으면 null을 사용한다.
JSON:
        
"""
    try:
        response = await llm.ainvoke(prompt)
        logger.debug(f"Entity extraction for routing raw response: {response.content}")
        cleaned_json_str = re.sub(r'```json\s*|\s*```', '', response.content.strip())
        entities = json.loads(cleaned_json_str)

        if not isinstance(entities, dict):
            return {}
        
        target_reason = entities.get("target_reason")
        logger.info(f"llm target_reason : {target_reason}")
        # --- End: Robust Target Determination Logic ---
        
        # LLM의 응답에서 추출된 값들을 통합, 'disease'를 'diseases' 리스트로 관리
        final_entities = {
            "location": entities.get("location") if entities.get("location") not in [None, "", "null", "None"] else None,
            "diseases": [], # 'diseases'를 리스트로 초기화
            "department": [], # department는 리스트로 시작
            "hospitals": [], # hospitals 리스트 초기화
            "doctors": [], # doctors 리스트 초기화
            "target":  entities.get("target") or "의사"
        }
        # LLM이 반환한 department와 disease를 각각의 리스트에 추가
        _add_unique_items(final_entities["department"], entities.get("department"))
        _add_unique_items(final_entities["diseases"], entities.get("disease"))



        # 민짱님께서 주신 우선순위 보강 로직 (department -> hospital -> disease -> doctors) 적용
        # 이 로직은 LLM 추출 결과 또는 entity_history에서 보강된 final_entities에 대해 적용됩니다.



        # location_history를 통한 location 보강 로직은 완전히 제거함.
        # final_entities["location"]은 이미 LLM 추출 또는 is_only_target_from_llm 조건에서 entity_history 보강을 통해 설정됨.
        # target은 LLM이 우선적으로 결정하도록 하고, 4순위 규칙에 따라 '의사'로 폴백되므로 여기서는 추가 보강하지 않습니다.

        logger.info(f"Extracted entities for routing: {final_entities}")
        return final_entities
    except Exception as e:
        logger.error(f"Entity extraction for routing failed: {e}", exc_info=True)
        return {}


async def extract_entities_for_routing_only_find_dept(llm: AzureChatOpenAI, state: dict) -> str | None:
    """
    Uses a targeted LLM call to extract only the latest 'department' from the conversation history,
    leveraging the persistent entity_history as well.
    """
    logger.info("Attempting to extract department with a specialized function...")
    
    # entity_history를 state에서 가져옵니다.
    entity_history = state.get("entity_history", {"hospitals": [], "doctors": [], "departments": [], "diseases": [], "location": None})

    def format_message_for_history(msg):
        if isinstance(msg, ToolMessage):
            return "ToolMessage: [Previous tool result]"
        else:
            return f"{type(msg).__name__}: {msg.content}"

    history_messages = [format_message_for_history(msg) for msg in state['messages'][-10:]]
    history = "\n".join(history_messages)

    # entity_history에서 departments 정보를 가져와 프롬프트에 추가
    entity_history_depts_info = ""
    if entity_history["departments"]:
        entity_history_depts_info = f"\n[Previous confirmed departments from entity_history]: {', '.join(entity_history['departments'])}"

    prompt = f"""You are a specialized entity extractor. Your only task is to find the most recently mentioned medical department from the conversation history provided below.
Also consider the previously confirmed departments from the entity_history.

[Conversation History]
{history}
{entity_history_depts_info}

[Instructions]
1. Read the entire conversation history AND the previously confirmed departments.
2. Identify the medical department (e.g., '가정의학과', '소아과', '내과').
3. If a department is mentioned, return only the name of the most recently mentioned or confirmed department.
4. If no department is mentioned or confirmed, return the word "None".
5. Do not provide any explanation or extra text. Only return the department name or "None".

Most recent department:"""
    
    try:
        response = await llm.ainvoke(prompt)
        department = response.content.strip()
        logger.debug(f"Specialized department extraction raw response: {department}")

        if department and department not in ["None", "null", ""]:
            logger.info(f"Specialized extractor found department: '{department}'")
            return department
        else:
            # 만약 LLM이 department를 추출하지 못했지만 entity_history에 department가 있다면 그것을 사용
            if entity_history["departments"]:
                logger.info(f"LLM did not find department, but using latest from entity_history: '{entity_history['departments'][-1]}'")
                return entity_history['departments'][-1]
            logger.info("Specialized extractor did not find a department, and entity_history is also empty.")
            return None
    except Exception as e:
        logger.error(f"Specialized department extraction failed: {e}", exc_info=True)
        return None


async def extract_entities_from_ai_response_and_update_history(
    llm: AzureChatOpenAI,
    ai_response_content: str,
    current_entity_history: Dict[str, List[Any]] # 현재 entity_history를 받아서 업데이트
) -> Dict[str, List[Any]]:
    """
    Extracts key medical entities from an AI's final response (content)
    and updates the entity history.
    """
    extracted_from_ai_response = await _extract_entities_from_text(llm, ai_response_content)

    # current_entity_history가 None일 경우 빈 딕셔너리로 초기화
    if current_entity_history is None:
        current_entity_history = {"hospitals": [], "doctors": [], "departments": [], "diseases": [], "location": None}

    _add_unique_items(current_entity_history["diseases"], extracted_from_ai_response.get("diseases"))
    _add_unique_items(current_entity_history["departments"], extracted_from_ai_response.get("departments"))
    _add_unique_items(current_entity_history["hospitals"], extracted_from_ai_response.get("hospitals"))
    
    current_entity_history["location"] = extracted_from_ai_response.get("location")
    # 의사 정보는 객체 형태로 저장되므로 별도 처리
    ai_doctors_as_obj = [{"name": name, "hospital": None, "department": None} for name in extracted_from_ai_response.get("doctors", [])]
    _add_unique_doctors(current_entity_history["doctors"], ai_doctors_as_obj)

    # logger.info(f"Updated entity history after AI response: {current_entity_history}")
    return current_entity_history