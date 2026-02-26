import os
import asyncio
import json
import uuid # 🚨 Add uuid for generating unique IDs
import re
from .tools.location_dic import LOCATION_NORMALIZATION_RULES, GROUP_LOCATION_EXPANSION_RULES
from .common.location_analyzer import classify_location_query, analyze_other_location_request, update_location_context
from .common.entity_analyzer import update_entity_context, extract_entities_for_routing, extract_entities_for_routing_only_find_dept, extract_entities_from_ai_response_and_update_history
from .common.utils import is_result_empty

from .common.geocoder import get_address_from_coordinates
from .introduce import EMERGENCY_INTRODUCTION 

from .common.handlers import classify_and_handle_initial_requests

from langchain_openai import AzureChatOpenAI
from langgraph.graph import StateGraph, END, START
from typing import TypedDict, Annotated, Literal, Optional, Optional
from .config import settings
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import MessagesState, add_messages
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from openai import APITimeoutError
from langchain_core.runnables.config import RunnableConfig

from .tools.tools import recommand_doctor, recommend_hospital, search_doctor, search_doctor_by_hospital, search_doctor_for_else_question
from .tools.tools import get_cached_tool_result # 내부 상태 조회를 위한 메타 도구
from .tools.sql_tool import (
    search_hospitals_by_location_and_department,
    search_hospital_by_disease_and_location,
    search_hospital_by_disease,
    search_hospital_by_disease_and_department,
    search_doctors_by_location_and_department,
    search_doctors_by_disease_and_location,
    search_by_location_only,
    search_doctors_by_department_only,
    search_doctors_by_disease_and_department,
    search_doctor_details_by_name,
    search_hospital_details_by_name,
    search_doctors_by_hospital_name
)
from .prompt.system_prompt import SYSTEM_PROMPT
from .prompt.validation_prompt import VALIDATION_PROMPT
from .common.logger import logger
from .tools.language_set import LANGUAGE_SET, LANGUAGE_GREETINGS, DEFAULT_GREETING
from .common.sanitizer import sanitize_prompt

import aiosqlite
import json
import uuid # 🚨 Add uuid for generating unique IDs

# 모델 설정
llm = AzureChatOpenAI(
    model_name=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0,
    request_timeout=settings.azure_request_timeout
)

llm_for_summary = AzureChatOpenAI(
    model_name=settings.azure_summary_api_model, # TODO: Use a cheaper model for summarization
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0,
    request_timeout=settings.azure_request_timeout
)

# 5개의 외부 정보 검색 도구
external_tools = [
    recommand_doctor, 
    recommend_hospital, 
    search_doctor, 
    search_doctor_by_hospital, 
    search_doctor_for_else_question,
    search_hospital_by_disease,
    search_doctors_by_department_only,
    search_doctors_by_disease_and_department,
    search_doctors_by_location_and_department,
    search_hospitals_by_location_and_department,
    search_doctors_by_disease_and_location,
    search_hospital_by_disease_and_location,
    search_hospital_by_disease_and_department,
    search_by_location_only,
    search_doctor_details_by_name,
    search_hospital_details_by_name,
    search_doctors_by_hospital_name
]
# 1개의 내부 상태 관리 도구
internal_tools = [get_cached_tool_result]

# LLM 바인딩을 위해 두 리스트 결합
tools = external_tools + internal_tools
tool_map = {t.name: t for t in tools}
model = llm.bind_tools(tools)


class AgentState(MessagesState):
    messages: Annotated[list, add_messages]
    locale: Annotated[str, ""]
    latitude: Optional[float]
    longitude: Optional[float]
    # The location_history stores a timeline of location contexts.
    # Each entry is a dict, e.g., {"type": "GPS", ...} or {"type": "CONTEXTUAL", ...}
    location_history: Annotated[list, []]
    # The entity_history stores a timeline of core medical entity contexts.
    entity_history: Annotated[dict, {}]
    # location: Annotated[Optional[str], None] # 추가: 명시적 location 필드 - entity_history로 이동
    retry: Annotated[int, 0]
    valid: Annotated[bool, False]
    summary_input_tokens: Annotated[int, 0]
    summary_output_tokens: Annotated[int, 0]
    summary_total_tokens: Annotated[int, 0]
    last_ai_message: Optional[str] # 이전 AI 메시지를 저장하기 위한 필드 추가

# 1️⃣ 에이전트 노드
async def agent_node(state: AgentState, config: RunnableConfig):
    """모델을 통해 답변하는 Agent 노드. 콘텐츠 필터 에러 발생 시, 메시지를 정제하여 재시도하는 로직을 포함합니다."""
    
    # 🚨 추가: last_ai_message가 state에 없을 경우 초기화
    if "last_ai_message" not in state:
        state["last_ai_message"] = None
    
    current_user_message = ""
    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        current_user_message = state["messages"][-1].content

    num_human_messages = len([msg for msg in state['messages'] if isinstance(msg, HumanMessage)])
    is_first_interaction_in_session = (num_human_messages == 1)
    locale = state.get("locale") or "ko"

    # --- START: 초기 요청 통합 처리 (현재 위치, 응급 상황, 금지된 추천) ---
    response = await classify_and_handle_initial_requests(state, config, current_user_message, is_first_interaction_in_session, locale)
    if response:
        return response
    # --- END: 초기 요청 통합 처리 ---

    # --- Start of New Location Context Management ---
    
    # 1. Call the new location context manager
    updated_history, clarification_question = await update_location_context(
        llm=llm_for_summary, # Pass the llm instance
        user_message=current_user_message,
        location_history=state.get('location_history', []),
        latitude=state.get('latitude'),
        longitude=state.get('longitude')
    )

    # 2. Update state with the new history
    state['location_history'] = updated_history

    # 엔트리 저장 여부  감지 (환경 변수 설정 시)
    # 🚨 START: AI의 최종 답변에서 엔티티 추출하여 entity_history 업데이트
    current_entity_history = state.get("entity_history")
    if current_entity_history is None:
        current_entity_history = {"hospitals": [], "doctors": [], "departments": [], "diseases": [], "location": None}
    
    # state["last_ai_message"]가 존재할 때만 엔티티 추출 로직 실행
    # --- START: 직전 AIMessage 분석을 통한 레거시 컨텍스트 추출
    ai_message_recommendation_info = "" # 최종 system prompt에 삽입될 정보
    last_ai_message_content = None # 직전 AIMessage의 content를 저장할 변수
    
    # 마지막 HumanMessage의 인덱스를 찾습니다.
    last_human_message_idx = -1
    for i in range(len( state["messages"]) - 1, -1, -1):
        if isinstance( state["messages"][i], HumanMessage):
            last_human_message_idx = i
            break

    # 마지막 HumanMessage 이전에 AIMessage가 있는지 확인합니다.
    # 즉, last_human_message_idx - 1 위치의 메시지가 AIMessage여야 합니다.
    if last_human_message_idx > 0 and isinstance( state["messages"][last_human_message_idx - 1], AIMessage):
        last_ai_message_content =  state["messages"][last_human_message_idx - 1].content
    # --- END: 직전 AIMessage 분석을 통한 레거시 컨텍스트 추출 ---

    last_ai_message_to_process = last_ai_message_content
    if last_ai_message_to_process:
        state["entity_history"] = await extract_entities_from_ai_response_and_update_history(
            llm=llm_for_summary,
            ai_response_content=last_ai_message_to_process,
            current_entity_history=current_entity_history
        )
    else:
        state["entity_history"] = current_entity_history # last_ai_message가 없으면 기존 entity_history 유지 또는 초기화
    # 🚨 END: AI의 최종 답변에서 엔티티 추출하여 entity_history 업데이트
    # --- End of New Entity Context Management ---

    # 3. If the manager returned a question, ask it immediately.
    if clarification_question:
        greeting = LANGUAGE_GREETINGS.get(locale, DEFAULT_GREETING)
        if is_first_interaction_in_session:
            clarification_question = f"{greeting}\n\n  {clarification_question}"
        logger.info(f"Asking clarification question from location manager: {clarification_question}")
        response = AIMessage(content=clarification_question)
        return {
            "messages": [response], 
            "retry": state.get("retry", 0), 
            "valid": True, 
            "location_history": state['location_history'],
            "entity_history": state['entity_history']
        }

    # 5. Prepare entity information to be injected into the main prompt as a structured JSON block
    persistent_facts_info = ""
    # 🚨 수정: state['entity_history']가 존재하고 내용이 있을 경우 시스템 프롬프트에 주입
    if state.get("entity_history") and any(state["entity_history"].values()):
        persistent_facts_info += f"""
/*
IMPORTANT: The following JSON block contains the latest confirmed entities from the conversation history.
The AI's previous message contained the following recommendations or suggestions. The user's current message might be an acceptance or follow-up on these. Prioritize the following information if the user's intent aligns with these details.
Inherited entities:
*/
{json.dumps(state["entity_history"], ensure_ascii=False, indent=2)}
"""
    
    messages = state["messages"] 

    # 1. 시스템 프롬프트에 필요한 정보 준비
    current_latitude = state.get('latitude')
    current_longitude = state.get('longitude')
    location_gps_info = ""
    if current_latitude is not None and current_longitude is not None:
        location_gps_info = f"\n\n/*\nIMPORTANT: User's current GPS location is available.\nLatitude: {current_latitude}\nLongitude: {current_longitude}\nPrioritize using location-based tools if the user asks for nearby facilities.\n*/\n"
    
    locale = state.get("locale") or "ko"
    language_name = LANGUAGE_SET.get(locale, LANGUAGE_SET["ko"])
    language_rule = f"\n\n**Response Language Rule**\n- The AI counselor's final response MUST be generated in **{language_name}**.\n"

    # 2. 기본 SYSTEM_PROMPT에서 기존 GPS 정보 블록 및 기타 불필요한 정보 제거
    # 혹시 모를 중복 방지를 위해 SYSTEM_PROMPT 자체를 클리닝
    clean_system_prompt_base = re.sub(r'\[사용자 현재 위치 정보 \(GPS\)\].*?\[지역 사전 분석 플래그\]', '', SYSTEM_PROMPT, flags=re.DOTALL)
    clean_system_prompt_base = re.sub(r'\n+/\*.*?IMPORTANT:.*?\*/\n*', '', clean_system_prompt_base, flags=re.DOTALL)
    clean_system_prompt_base = re.sub(r'\[지역 사전 분석 플래그\].*?---', '', clean_system_prompt_base, flags=re.DOTALL)
    clean_system_prompt_base = re.sub(r'\[Location Context\].*?\}', '', clean_system_prompt_base, flags=re.DOTALL)

    # 3. 최종 시스템 프롬프트 구성
    final_system_prompt_content = clean_system_prompt_base + persistent_facts_info + location_gps_info + language_rule
    
    # 4. messages 리스트에서 기존 SystemMessage를 모두 제거
    messages[:] = [msg for msg in messages if not isinstance(msg, SystemMessage)]
    
    # 5. 최종 구성된 SystemMessage를 맨 앞에 삽입
    messages.insert(0, SystemMessage(content=final_system_prompt_content))

    # --- START: ToolMessage 마이그레이션 (압축 및 캐싱) ---
    # 대용량 ToolMessage를 SQLite에 저장하고 요약 정보로 대체하여 토큰을 절약합니다.
    if settings.llm_summary_verbose:
        session_id = config["configurable"]["thread_id"]
        async with aiosqlite.connect(settings.sqlite_directory, check_same_thread=False) as conn:
            # 현재 턴에서 방금 실행된 최신 ToolMessage는 마이그레이션에서 제외한다.
            latest_tool_messages_indices = set()
            if len(messages) > 1 and isinstance(messages[-1], ToolMessage):
                if isinstance(messages[-2], AIMessage) and messages[-2].tool_calls:
                    num_tool_calls = len(messages[-2].tool_calls)
                    for i in range(num_tool_calls):
                        idx_to_exclude = len(messages) - 1 - i
                        if idx_to_exclude >= 0 and isinstance(messages[idx_to_exclude], ToolMessage):
                             latest_tool_messages_indices.add(idx_to_exclude)
                        else:
                            break

            # 메시지 리스트를 순회하며 '과거의' ToolMessage만 마이그레이션
            for i in range(len(messages) - 1, -1, -1):
                if i in latest_tool_messages_indices:
                    continue
                msg = messages[i]
                if isinstance(msg, ToolMessage):
                    try:
                        tool_content_json = json.loads(msg.content)
                        # 🚨 [BUG FIX] 이미 마이그레이션되었거나, 복원된 컨텍스트는 다시 마이그레이션하지 않음 (DB 덮어쓰기 방지)
                        if not isinstance(tool_content_json, dict) or tool_content_json.get("migrated") is True or tool_content_json.get("is_historical_context") is True:
                            continue
                        
                        original_content = msg.content
                        result_id = str(uuid.uuid4())
                        await conn.execute(
                            "INSERT OR REPLACE INTO tool_results_cache (session_id, result_id, content) VALUES (?, ?, ?)",
                            (session_id, result_id, original_content)
                        )
                        await conn.commit()
                        
                        placeholder_summary = "과거 도구 실행 결과가 외부에 저장되었습니다."
                        param_dict = {}
                        if 'chat_type' in tool_content_json:
                            answer_content = tool_content_json.get('answer')
                            if isinstance(answer_content, dict):
                                summary_parts = []
                                count_info = ""
                                if answer_content.get('disease'):
                                    summary_parts.append(f"질환: {answer_content['disease']}")
                                    param_dict['disease'] = answer_content['disease']
                                if answer_content.get('department'):
                                    summary_parts.append(f"진료과: {answer_content['department']}")
                                    param_dict['department'] = answer_content['department']
                                if answer_content.get('hospital'):
                                    summary_parts.append(f"병원: {answer_content['hospital']}")
                                    param_dict['hospital'] = answer_content['hospital']
                                elif answer_content.get('hospitals') and len(answer_content['hospitals']) > 0:
                                    first_hosp = answer_content['hospitals'][0].get('name', '')
                                    if first_hosp: summary_parts.append(f"주요 병원: {first_hosp}")
                                    param_dict['hospital'] = first_hosp
                                    param_dict['hospital_count'] = len(answer_content['hospitals'])
                                if answer_content.get('doctors') and len(answer_content['doctors']) > 0:
                                    first_doc = answer_content['doctors'][0].get('name', '')
                                    if first_doc: summary_parts.append(f"주요 의사: {first_doc}")
                                    param_dict['doctor'] = first_doc
                                    param_dict['doctor_count'] = len(answer_content['doctors'])
                                
                                if answer_content.get('doctors'): count_info = f"{len(answer_content['doctors'])}명의 의사 정보"
                                elif answer_content.get('hospitals'): count_info = f"{len(answer_content['hospitals'])}개의 병원 정보"

                                if summary_parts or count_info:
                                    placeholder_summary = f"과거 {tool_content_json['chat_type']} 결과: {count_info}{' (' + ', '.join(summary_parts) + ')' if summary_parts else ''}"
                            elif isinstance(answer_content, str):
                                placeholder_summary = f"과거 {tool_content_json['chat_type']} 결과: {answer_content[:100]}... (저장됨)"

                        msg.content = json.dumps({
                            "migrated": True,
                            "result_id": result_id,
                            "summary": placeholder_summary,
                            "param": param_dict
                        }, ensure_ascii=False)
                        logger.info(f"ToolMessage migrated. result_id: {result_id}, summary: {placeholder_summary}")
                    except Exception as e:
                        logger.error(f"Error during ToolMessage migration: {e}")
    # --- END: ToolMessage 마이그레이션 ---

    # --- START: Proactive Refined Restoration (선제적 핵심 정보 복원) ---
    # 대화가 길어질 경우(2턴 이상), 과거 캐시에서 핵심 엔티티만 뽑아 미리 복원하여 토큰을 아끼고 정확도를 높입니다.
    session_id = config["configurable"]["thread_id"]
    migrated_tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage) and '"migrated": true' in msg.content]
    
    if len(migrated_tool_messages) > 0:
        limit = settings.proactive_restoration_limit
        logger.info(f"--- [PROACTIVE RESTORATION] {len(migrated_tool_messages)}개의 캐시 중 최근 {limit}개 핵심 정보 복원 시도 ---")
        async with aiosqlite.connect(settings.sqlite_directory, check_same_thread=False) as conn:
            # 설정된 리미트만큼 최근 migrated 메시지만 처리
            target_messages = migrated_tool_messages[-limit:]
            for idx, msg in enumerate(target_messages, 1):
                try:
                    content_data = json.loads(msg.content)
                    result_id = content_data.get("result_id")
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "SELECT content FROM tool_results_cache WHERE result_id = ? AND session_id = ?",
                            (result_id, session_id)
                        )
                        row = await cursor.fetchone()
                        if row:
                            full_content = json.loads(row[0])
                            chat_type = full_content.get("chat_type") or "unknown"
                            answer = full_content.get('answer', {})
                            if isinstance(answer, dict):
                                # 🚨 핵심 정보 추출 (화제 전환 판단 및 정확도 향상을 위해 정보 보강)
                                doctors = []
                                for d in answer.get("doctors", []):
                                    name = d.get("name") or d.get("doctorname")
                                    if name:
                                        doctors.append({
                                            "name": name,
                                            "hospital": d.get("hospital") or d.get("shortname") or d.get("hospital_name"),
                                            "deptname": d.get("deptname"),
                                            "specialties": d.get("specialties")
                                        })
                                
                                hospitals = [h.get("shortname") or h.get("name") for h in answer.get("hospitals", []) if h.get("shortname") or h.get("name")]
                                
                                refined_context = {
                                    "historical_reference_type": chat_type,
                                    "entities_found_in_this_step": {
                                        "doctors": doctors,
                                        "hospitals": hospitals,
                                        "disease_context": answer.get("disease") or answer.get("standard_spec"),
                                        "department_context": answer.get("department")
                                    }
                                }
                                # 🚨 [CRITICAL FIX] "migrated": True와 result_id를 유지하여 무한 압축 방지
                                msg.content = json.dumps({
                                    "migrated": True, 
                                    "is_historical_context": True,
                                    "result_id": result_id,
                                    "content_summary": content_data.get("summary"),
                                    "data": refined_context
                                }, ensure_ascii=False)
                                logger.info(f"✅ [# {idx}] 과거 컨텍스트 복원 완료: {chat_type} (의사 {len(doctors)}명)")
                                logger.info(f"   ㄴ [엔티티]: {refined_context['entities_found_in_this_step']}")
                            else:
                                logger.info(f"ℹ️ [# {idx}] 복원 스킵: 데이터 구조 불일치 ({chat_type})")
                except Exception as e:
                    logger.warning(f"⚠️ Proactive restoration failed for a message: {e}")
    # --- END: Proactive Refined Restoration ---

    # 🚨 START: 이전 턴에서 발생한 에러 AIMessage를 제거하여 컨텍스트를 클린하게 유지합니다.
    cleaned_messages = []
    error_patterns = [
        "죄송합니다. 서비스 처리 중 예상치 못한 오류가 발생했습니다.",
        "죄송합니다. AI 콘텐츠 필터링 정책으로 답변이 일시 중단되었습니다. 표현을 바꿔 다시 질문해주세요."
    ]
    
    last_human_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            last_human_idx = i
            
    for i, msg in enumerate(messages):
        if i <= last_human_idx:
            cleaned_messages.append(msg)
        elif isinstance(msg, AIMessage):
            is_error_message = any(pattern in msg.content for pattern in error_patterns)
            if not is_error_message:
                cleaned_messages.append(msg)
            else:
                logger.info(f"Removed previous error AIMessage from context: {msg.content}")
        else:
            cleaned_messages.append(msg)
            
    state["messages"] = cleaned_messages
    messages = state["messages"]
    # 🚨 END: 에러 AIMessage 제거 로직

    # --- START: Try-Catch-Retry & Cache Restoration Loop ---
    intermediate_messages = [] 
    try:
        logger.info("Calling model with original message...")
        response = await model.ainvoke(messages, config)
        
        # 🚨 [NEW] 내부 캐시 복원 루프: LLM이 get_cached_tool_result를 호출하면 즉시 내부에서 처리하고 모델을 재호출합니다.
        if isinstance(response, AIMessage) and response.tool_calls:
            cache_calls = [tc for tc in response.tool_calls if tc['name'] == 'get_cached_tool_result']
            if cache_calls:
                logger.info("┌──────────────────────────────────────────────────────────┐")
                logger.info(f"│ 🛠️  [INTERNAL_LOOP] 추가 캐시 복원 요청 감지 ({len(cache_calls)}건) │")
                logger.info("└──────────────────────────────────────────────────────────┘")
                intermediate_messages.append(response)
                
                loop_messages = list(messages)
                loop_messages.append(response)
                
                session_id = config["configurable"]["thread_id"]
                async with aiosqlite.connect(settings.sqlite_directory, check_same_thread=False) as conn:
                    for tc in cache_calls:
                        result_id = tc['args'].get('result_id')
                        if result_id:
                            async with conn.cursor() as cursor:
                                await cursor.execute(
                                    "SELECT content FROM tool_results_cache WHERE result_id = ? AND session_id = ?",
                                    (result_id, session_id)
                                )
                                row = await cursor.fetchone()
                                if row:
                                    logger.info(f"✅ 캐시 데이터 원본 복원 성공 (result_id: {result_id})")
                                    tool_msg = ToolMessage(content=row[0], tool_call_id=tc['id'])
                                    loop_messages.append(tool_msg)
                                    intermediate_messages.append(tool_msg)
                                else:
                                    logger.warning(f"❌ 캐시 데이터를 찾을 수 없음 (result_id: {result_id})")
                                    tool_msg = ToolMessage(content=json.dumps({"error": "Cache not found"}), tool_call_id=tc['id'])
                                    loop_messages.append(tool_msg)
                                    intermediate_messages.append(tool_msg)
                
                logger.info("🔄 복원된 데이터를 포함하여 모델 즉시 재호출 중...")
                response = await model.ainvoke(loop_messages, config)
                logger.info("✨ 모델 재호출 완료.")
        # 🚨 [END] 내부 캐시 복원 루프

    except Exception as e:
        logger.warning(f"LLM call failed with error: {e}. Checking for content filter.")
        error_message = str(e)
        
        if "An assistant message with 'tool_calls' must be followed by tool messages" in error_message or \
           "tool_call_ids did not have response messages" in error_message or \
           ("invalid_request_error" in error_message and "tool_calls" in error_message):
            
            logger.error(f"Detected invalid_request_error related to tool_calls.")
            last_ai_message_idx = -1
            for i in range(len(state["messages"]) - 1, -1, -1):
                if isinstance(state["messages"][i], AIMessage):
                    last_ai_message_idx = i
                    break
            
            if last_ai_message_idx != -1:
                state["messages"][last_ai_message_idx].tool_calls = []
                state["messages"][last_ai_message_idx].content = "처리 중 문제가 발생하여 요청을 완료하지 못했습니다. 다시 시도해 주시거나 다른 질문을 해주세요."
            
            return {
                "messages": state["messages"],
                "retry": state.get("retry", 0),
                "location_history": state['location_history'],
                "entity_history": state['entity_history']
            }
        
        if "content management policy" in error_message or "content filter" in error_message:
            logger.warning("Original message filtered. Retrying with sanitized message.")
            original_user_message = next((msg.content for msg in reversed(state["messages"]) if isinstance(msg, HumanMessage)), "")
            sanitized_content = sanitize_prompt(original_user_message)
            
            sanitized_messages_for_llm = list(state["messages"])
            for i in range(len(sanitized_messages_for_llm) - 1, -1, -1):
                if isinstance(sanitized_messages_for_llm[i], HumanMessage):
                    sanitized_messages_for_llm[i] = HumanMessage(content=sanitized_content, id=sanitized_messages_for_llm[i].id)
                    break
            
            try:
                response = await model.ainvoke(sanitized_messages_for_llm, config)
            except Exception as retry_e:
                logger.error(f"Retry failed: {retry_e}")
                fallback_message = "죄송합니다. AI 콘텐츠 필터링 정책으로 답변이 일시 중단되었습니다. 표현을 바꿔 다시 질문해주세요."
                response = AIMessage(content=fallback_message)
        else:
            logger.error(f"Non-filter error: {e}")
            response = AIMessage(content="처리 중 문제가 발생하여 요청을 완료하지 못했습니다. 다시 시도해 주시거나 다른 질문을 해주세요.")
    # --- END: Try-Catch-Retry & Cache Restoration Loop ---

    locale = state.get("locale") or "ko"
    greeting = LANGUAGE_GREETINGS.get(locale, DEFAULT_GREETING)
    if is_first_interaction_in_session and not response.content.strip().startswith(greeting):
        response.content = f"{greeting}\n\n {response.content}"

    state["last_ai_message"] = response.content 

    return {
        "messages": intermediate_messages + [response], 
        "retry": state.get("retry", 0),
        "location_history": state['location_history'],
        "entity_history": state['entity_history'],
        "last_ai_message": state["last_ai_message"]
    }



# 2️⃣ 분기 엣지
def should_continue(state: AgentState):
    """모델 답변에 따라 분기 처리하는 edge"""
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "validate"
    
# 3️⃣ 검증 노드
async def validate_node(state: AgentState) -> AgentState:
    """응답의 적절성 여부 판단"""
    retry = state.get("retry", 0)

    if not settings.validation_enable or retry >= settings.validation_retry_limit:
        return {"messages": state["messages"], "retry": 0, "valid": True}

    answer = state["messages"][-1].content
    question = ""
    for msg in reversed(state["messages"]):
        if msg.type == "human":
            question = msg.content
            break

    prompt = VALIDATION_PROMPT.format(question=question, answer=answer)
    result = await llm.ainvoke(prompt)
    is_valid = result.content.strip().lower() == "yes"
       
    if is_valid:
        return {"messages": state["messages"], "retry": 0, "valid": True}
       
    new_messages = []
    reverse_messages = []
    found = False
    for msg in reversed(state["messages"]):
        if found:
            reverse_messages.append(msg)
        if not found and isinstance(msg, HumanMessage):
            reverse_messages.append(msg)
            found = True

    new_messages = list(reversed(reverse_messages))
    
    state["messages"].clear()
    state["messages"].extend(new_messages)
    
    return {"messages": state["messages"], "retry": retry + 1, "valid": False}

async def custom_tool_node(state: AgentState):
    """
    Custom tool node that intelligently routes calls to the appropriate tool for performance.
    It handles three types of location searches: user-centric, named location, and named location proximity.
    """
    tool_messages = []
    last_message = state["messages"][-1]
    
    locale = state.get("locale") or "ko"
    language_name = LANGUAGE_SET.get(locale, LANGUAGE_SET["ko"])

    latitude = state.get("latitude")
    longitude = state.get("longitude")

    original_query = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            original_query = msg.content
            break
    
    # --- NLP 기반 위치 분석: agent_node에서 이미 분석/저장한 location_history 값을 사용 ---
    is_proximity_query = False
    classification = "NONE"
    anchor_noun = None
    
    location_history = state.get('location_history', [])
    if location_history:
        last_location = location_history[-1]
        if last_location.get('type') == 'GPS':
            is_proximity_query = True
            classification = "USER_LOCATION"
        elif last_location.get('type') == 'CONTEXTUAL':
            is_proximity_query = last_location.get('is_nearby', False)
            classification = "NAMED_LOCATION"
            # 컨텍스트에 저장된 위치 명사를 anchor_noun으로 사용
            anchor_noun = last_location.get('sigungu') or last_location.get('sido')
    # ---

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        observation = None
        routed = False
        llm_legacy_list = tool_args.get('legacy_list')
        llm_proposal = tool_args.get('proposal')
        llm_request_recommend = tool_args.get('is_request_recommend')
        llm_request_distance = tool_args.get('llm_request_distance')
        logger.info(f"ll, select legacy_list : '{llm_legacy_list}'")
        logger.info(f"ll, select is_request_recommend : '{llm_request_recommend}'")
        logger.info(f"ll, select proposal : '{llm_proposal}'")
        logger.info(f"llm select llm_request_distance : '{llm_request_distance}'")
        logger.info(f"llm select tool_name : '{tool_name}'")
        # --- [PROXIMITY_INJECTION] ---
        # is_location_near 파라미터를 받는 툴이라면, is_proximity_query 값을 주입
        if tool_name in [
            "search_hospitals_by_location_and_department",
            "search_doctors_by_location_and_department",
            "search_doctors_by_disease_and_location",
            "search_hospital_by_disease_and_location",
            "search_by_location_only"
        ]:
            # LLM이 is_location_near 값을 생성하지 않았거나 잘못 생성한 경우,
            # NLP 분석 결과를 기반으로 강제 주입/수정합니다.
            if tool_args.get('is_location_near') != is_proximity_query:
                 logger.info(f"Tool '{tool_name}'의 is_location_near 값을 {is_proximity_query}(으)로 강제 주입/수정합니다.")
                 tool_args['is_location_near'] = is_proximity_query
        # --- [PROXIMITY_INJECTION_END] ---
        
        if tool_name == "search_doctor_for_else_question":
            # --- START of Comprehensive Routing Logic ---
            entities = await extract_entities_for_routing(llm_for_summary, state)
            loc = entities.get('location')
            dis = entities.get('disease')
            dep = entities.get('department')
            target = entities.get('target', '의사')

            # 라우팅을 위한 지역(location) 결정 로직
            # 1. LLM이 최신 대화에서 명시적인 지역('loc')을 추출했는지 확인합니다.
            # 2. 만약 명시적인 지역이 없다면(if not loc), 전체 대화 맥락에서 파악된 기준 명사('anchor_noun')를 사용합니다.
            #    이를 통해 '거기 근처'와 같은 맥락적 질문에 대응할 수 있습니다.
            if not loc and classification == "NAMED_LOCATION" and anchor_noun:
                logger.info(f"Explicit 'loc' not found. Falling back to anchor_noun from context: '{anchor_noun}'")
                loc = anchor_noun # NLP가 추출한 명사를 폴백으로 사용

            logger.info(f"is_proximity_query: '{is_proximity_query}' (classification: {classification})")

            # Case 1: User-centric proximity search (e.g., "near me + department")
            # 'loc'이 없고, 'dep' 또는 'dis'가 있으며, 사용자 위치 기반 검색일 때
            if not loc and (dep or dis) and classification == "USER_LOCATION":
                params = {'latitude': latitude, 'longitude': longitude, 'is_location_near': True, 'limit': tool_args.get('limit')}
                if dep:
                    tool_key = 'search_doctors_by_location_and_department' if '의사' in target else 'search_hospitals_by_location_and_department'
                    params['department'] = dep
                    try:
                        if tool_key == 'search_doctors_by_location_and_department':
                            params['proposal'] = llm_proposal or ""
                            observation = await search_doctors_by_location_and_department.ainvoke(params)
                        elif tool_key == 'search_hospitals_by_location_and_department':
                            observation = await search_hospitals_by_location_and_department.ainvoke(params)
                    except Exception as e:
                        observation = f"Error executing routed tool {tool_key}: {e}"
                    routed = True
                elif dis:
                    tool_key = 'search_doctors_by_disease_and_location' if '의사' in target else 'search_hospital_by_disease_and_location'
                    params['disease'] = dis
                    try:
                        if tool_key == 'search_doctors_by_disease_and_location':
                            params['proposal'] = llm_proposal or ""
                            observation = await search_doctors_by_disease_and_location.ainvoke(params)
                        elif tool_key == 'search_hospital_by_disease_and_location':
                            observation = await search_hospital_by_disease_and_location.ainvoke(params)
                    except Exception as e:
                        observation = f"Error executing routed tool {tool_key}: {e}"
                    routed = True

            # Case 2.5: Named location, disease, and department search (e.g., "Ulsan에서 당뇨병 성형외과 잘하는 병원")
            elif loc and dis and dep and target == '병원':
                params = {'location': loc, 'disease': dis, 'department': dep, 'is_location_near': is_proximity_query, 'limit': tool_args.get('limit')}
                tool_key = 'search_hospital_by_disease_and_department'
                logger.info(f"Routing (Named Loc + Disease + Dept): {tool_key}")
                try:
                    observation = await search_hospital_by_disease_and_department.ainvoke(params)
                except Exception as e:
                    observation = f"Error executing routed tool {tool_key}: {e}"
                routed = True
            
            # 🚨 [NEW] Case 2.6: Disease and department search without location for doctors
            elif dis and dep and not loc and '의사' in target:
                params = {'disease': dis, 'department': dep, 'limit': tool_args.get('limit'), 'proposal': llm_proposal or ""}
                tool_key = 'search_doctors_by_disease_and_department'
                logger.info(f"Routing (Disease + Dept, No Loc): {tool_key}")
                try:
                    observation = await search_doctors_by_disease_and_department.ainvoke(params)
                except Exception as e:
                    observation = f"Error executing routed tool {tool_key}: {e}"
                routed = True
            # Case 2: Named location search (e.g., "in Ulsan + dept" or "near Ulsan + dept")
            elif loc and (dep or dis):
                # is_proximity_query는 NLP 분석 결과로 이미 계산됨
                params = {'location': loc, 'is_location_near': is_proximity_query, 'limit': tool_args.get('limit')}
                if dep:
                    tool_key = 'search_doctors_by_location_and_department' if '의사' in target else 'search_hospitals_by_location_and_department'
                    params['department'] = dep
                    try:
                        if tool_key == 'search_doctors_by_location_and_department':
                            params['proposal'] = llm_proposal or ""
                            observation = await search_doctors_by_location_and_department.ainvoke(params)
                        elif tool_key == 'search_hospitals_by_location_and_department':
                            observation = await search_hospitals_by_location_and_department.ainvoke(params)
                    except Exception as e:
                        observation = f"Error executing routed tool {tool_key}: {e}"
                    routed = True
                elif dis:
                    tool_key = 'search_doctors_by_disease_and_location' if '의사' in target else 'search_hospital_by_disease_and_location'
                    params['disease'] = dis
                    try:
                        if tool_key == 'search_doctors_by_disease_and_location':
                            params['proposal'] = llm_proposal or ""
                            observation = await search_doctors_by_disease_and_location.ainvoke(params)
                        elif tool_key == 'search_hospital_by_disease_and_location':
                            observation = await search_hospital_by_disease_and_location.ainvoke(params)
                    except Exception as e:
                        observation = f"Error executing routed tool {tool_key}: {e}"
                    routed = True

            # 🚨 [NEW] Case 3: Location-only search
            elif loc and not dep and not dis:
                params = {
                    'location': loc,
                    'target': target,
                    'is_location_near': is_proximity_query,
                    'limit': tool_args.get('limit')
                }
                # '내 근처'와 같은 사용자 위치 기반 검색 시, 명시적 지역명이 없더라도 GPS 좌표를 사용
                if classification == "USER_LOCATION" and latitude is not None and longitude is not None:
                    params['latitude'] = latitude
                    params['longitude'] = longitude

                tool_key = 'search_by_location_only'
                params['proposal'] = llm_proposal or ""
                logger.info(f"Routing (Location Only): {tool_key} with params: {params}")
                try:
                    observation = await search_by_location_only.ainvoke(params)
                except Exception as e:
                    observation = f"Error executing routed tool {tool_key}: {e}"
                routed = True
            
            # 🚨 [NEW] Case 4: disease-only search
            elif dis and not loc:
                if target == '병원':
                    tool_key = 'search_hospital_by_disease'
                    params = {'disease': dis, 'limit': tool_args.get('limit')}
                    logger.info(f"Routing (Disease Only, Hospital): {tool_key} with params: {params}")
                    try:
                        observation = await search_hospital_by_disease.ainvoke(params)
                    except Exception as e:
                        observation = f"Error executing routed tool {tool_key}: {e}"
                    routed = True
            # 🚨 [NEW] Case 5: Department-only search for Doctors
            elif dep and not loc and not dis and target == '의사':
                params = {'department': dep, 'limit': tool_args.get('limit')}
                params['proposal'] = llm_proposal or ""
                tool_key = 'search_doctors_by_department_only'
                logger.info(f"Routing (Department Only, Doctors): {tool_key} with params: {params}")
                try:
                    observation = await search_doctors_by_department_only.ainvoke(params)
                except Exception as e:
                    observation = f"Error executing routed tool {tool_key}: {e}"
                routed = True
            # --- END of Comprehensive Routing Logic ---

        # 🚨 NEW: Enrichment logic for search_doctor_by_hospital
        elif tool_name == "search_doctor_by_hospital":
            # Check if department is missing from the LLM's tool_args
            if not tool_args.get("deptname"): # Use 'deptname' as indicated by the user's log
                logger.info(f"Tool '{tool_name}' called without 'deptname'. Attempting to enrich from context.")
                context_dept = await extract_entities_for_routing_only_find_dept(llm_for_summary, state)
                if context_dept:
                    logger.info(f"Enriching '{tool_name}' call with department: '{context_dept}'")
                    tool_args["deptname"] = context_dept # Inject the department
            
            # Execute the (potentially enriched) search_doctor_by_hospital tool
            tool_to_call = tool_map.get(tool_name)
            if not tool_to_call:
                observation = f"Tool {tool_name} not found."
            else:
                try:
                    tool_args['proposal'] = llm_proposal or ""
                    observation = await tool_to_call.ainvoke(tool_args)
                except Exception as e:
                    observation = f"Error executing tool {tool_name}: {e}"
            routed = True # Mark as handled to skip the default execution below

        if not routed:
            tool_to_call = tool_map.get(tool_name)
            if not tool_to_call:
                observation = f"Tool {tool_name} not found."
            else:
                if tool_name == "search_doctor_for_else_question":
                    logger.info("No specific pattern matched. Falling back to search_doctor_for_else_question (SQL Agent).")
                    #language_instruction = f"\n\n[중요] 최종 답변은 반드시 {language_name}(으)로, 자연스러운 문장으로 만들어주세요."
                    #if language_instruction not in tool_args.get("question", ""):
                    #     tool_args["question"] += language_instruction
                    
                    # NLP 분석 결과에 따라 GPS 좌표 전달
                    if classification == "USER_LOCATION":
                        if latitude is not None: tool_args["latitude"] = latitude
                        if longitude is not None: tool_args["longitude"] = longitude
                    # NAMED_LOCATION 이나 NONE 의 경우, SQL Agent가 알아서 처리하도록 위임 (좌표 전달 안함)
                    else:
                        tool_args.pop("latitude", None)
                        tool_args.pop("longitude", None)
                    tool_args['proposal'] = llm_proposal or ""

                elif tool_name == "recommend_hospital":
                    if latitude is not None: tool_args['latitude'] = latitude
                    if longitude is not None: tool_args['longitude'] = longitude
                    tool_args['is_nearby'] = is_proximity_query
                elif tool_name == "recommand_doctor":
                    # recommand_doctor는 'disease'가 필수. 'disease'가 없으면 entity_history 기반 라우팅 시도
                    if not tool_args.get('disease'):
                        logger.info(f"Tool '{tool_name}' called without 'disease'. Attempting to route using entity_history and current context.")
                        
                        # entity_history에서 최신 엔티티 정보 추출
                        entities_from_history = await extract_entities_for_routing(llm_for_summary, state)
                        loc_from_history = entities_from_history.get('location')
                        dep_from_history = entities_from_history.get('department')
                        dis_from_history = entities_from_history.get('diseases') # 질환명도 가져옴 (리스트 형태)
                        target_from_history = entities_from_history.get('target', '의사')

                        # 라우팅을 위한 파라미터 초기화
                        fallback_tool_key = None
                        fallback_params = {}

                        # 라우팅 우선순위: department -> disease -> location
                        # 1. entity_history에 department가 있는 경우
                        if dep_from_history:
                            logger.info(f"[recommand_doctor Fallback] Department found in history: {dep_from_history}. Routing to department-based search.")
                            fallback_params = {'department': dep_from_history, 'limit': tool_args.get('limit', 10), 'proposal': llm_proposal or ""}
                            
                            # 위치 정보가 있으면 위치 기반 검색, 없으면 진료과만으로 검색
                            has_location_info = (latitude is not None and longitude is not None) or (loc_from_history and loc_from_history.strip())
                            if has_location_info:
                                fallback_tool_key = 'search_doctors_by_location_and_department'
                                if latitude is not None: fallback_params['latitude'] = latitude
                                if longitude is not None: fallback_params['longitude'] = longitude
                                if loc_from_history: fallback_params['location'] = loc_from_history
                                fallback_params['is_location_near'] = is_proximity_query # is_proximity_query는 custom_tool_node 시작 부분에서 이미 계산됨
                            else:
                                fallback_tool_key = 'search_doctors_by_department_only'
                        
                        # 2. department는 없고 disease가 entity_history에 있는 경우
                        elif dis_from_history:
                            logger.info(f"[recommand_doctor Fallback] Disease found in history: {dis_from_history}. Routing to disease-based search.")
                            # '의사' 타겟이므로 search_doctors_by_disease_and_location 사용
                            fallback_tool_key = 'search_doctors_by_disease_and_location'
                            fallback_params = {'disease': dis_from_history, 'limit': tool_args.get('limit', 10), 'proposal': llm_proposal or ""}
                            
                            has_location_info = (latitude is not None and longitude is not None) or (loc_from_history and loc_from_history.strip())
                            if has_location_info:
                                if latitude is not None: fallback_params['latitude'] = latitude
                                if longitude is not None: fallback_params['longitude'] = longitude
                                if loc_from_history: fallback_params['location'] = loc_from_history
                                fallback_params['is_location_near'] = is_proximity_query
                            # 이 경우 disease만으로는 진료과 추론이 필요할 수 있으나, 일단 직접 호출 시도
                        
                        # 3. department, disease는 없고 location만 entity_history에 있는 경우
                        elif loc_from_history:
                            logger.info(f"[recommand_doctor Fallback] Only Location found in history: {loc_from_history}. Routing to search_by_location_only.")
                            fallback_tool_key = 'search_by_location_only'
                            fallback_params = {
                                'location': loc_from_history,
                                'target': target_from_history,
                                'is_location_near': is_proximity_query,
                                'limit': tool_args.get('limit'),
                                'proposal': llm_proposal or ""
                            }
                            if classification == "USER_LOCATION" and latitude is not None and longitude is not None:
                                fallback_params['latitude'] = latitude
                                fallback_params['longitude'] = longitude

                        if fallback_tool_key:
                            try:
                                # department/disease 인자가 리스트인 경우 첫 번째 요소만 사용하도록 조정
                                if 'department' in fallback_params and isinstance(fallback_params['department'], list):
                                    fallback_params['department'] = fallback_params['department'][0] if fallback_params['department'] else None
                                if 'disease' in fallback_params and isinstance(fallback_params['disease'], list):
                                    fallback_params['disease'] = fallback_params['disease'][0] if fallback_params['disease'] else None

                                cleaned_fallback_params = {k: v for k, v in fallback_params.items() if v is not None}
                                logger.info(f"[recommand_doctor Fallback] Executing fallback tool '{fallback_tool_key}' with params: {cleaned_fallback_params}")
                                
                                # 라우팅된 도구 호출
                                if fallback_tool_key == 'search_doctors_by_location_and_department':
                                    observation = await search_doctors_by_location_and_department.ainvoke(cleaned_fallback_params)
                                elif fallback_tool_key == 'search_doctors_by_department_only':
                                    observation = await search_doctors_by_department_only.ainvoke(cleaned_fallback_params)
                                elif fallback_tool_key == 'search_doctors_by_disease_and_location':
                                    observation = await search_doctors_by_disease_and_location.ainvoke(cleaned_fallback_params)
                                elif fallback_tool_key == 'search_by_location_only':
                                    observation = await search_by_location_only.ainvoke(cleaned_fallback_params)
                                # 필요한 경우 다른 도구도 여기에 추가
                                
                                routed = True
                            except Exception as e:
                                logger.error(f"Error executing routed fallback tool {fallback_tool_key}: {e}", exc_info=True)
                                observation = {"chat_type": "general", "answer": f"도구 실행 중 오류 발생: {str(e)}"}
                                routed = True # 에러 발생 시에도 라우팅된 것으로 처리하여 기본 호출 방지

                    if not routed: # Fallback 라우팅이 되지 않은 경우에만 원래 recommand_doctor 호출 (disease가 여전히 없음)
                        # 이 시점에서는 disease가 여전히 없으므로, recommand_doctor가 질환명을 요청하는 메시지를 반환할 것임.
                        # 이는 기존 동작이므로 유지.
                        if latitude is not None: tool_args['latitude'] = latitude
                        if longitude is not None: tool_args['longitude'] = longitude
                        tool_args['proposal'] = llm_proposal or ""


                
                try:
                    # 여기에 추가
                    if tool_name == "search_doctor_for_else_question":
                        tool_args["use_json_output"] = True 

                    observation = await tool_to_call.ainvoke(tool_args)
                except Exception as e:
                    observation = f"Error executing tool {tool_name}: {e}"

        # 🚨 Add this line to define is_empty_result
        is_empty_result = is_result_empty(tool_name, observation) # tool_name과 observation을 사용하여 결과가 비었는지 확인
        logger.info(f"is_empty_result {is_empty_result} 값여부에 따라 판단 필요")
        if observation and isinstance(observation, dict):
            # 💡 front_sort_type 주입 로직
            if 'answer' in observation and isinstance(observation['answer'], dict):
                if tool_name == "recommand_doctor":
                    observation['answer']['front_sort_type'] = "evaluation"
                    logger.info(f"Injected 'front_sort_type': 'evaluation' for recommand_doctor tool.")
                else:
                    observation['answer']['front_sort_type'] = "distance" if is_proximity_query else "evaluation"
                    logger.info(f"Injected 'front_sort_type': '{observation['answer']['front_sort_type']}' based on proximity query flag.")
            if llm_request_distance == 'True':
                     observation['answer']['front_sort_type'] = 'distance'
            effective_tool_name = observation.get('chat_type', tool_name)

            if is_empty_result: # 이곳에서 is_empty_result가 사용됨
                logger.info(f"Tool {effective_tool_name} returned empty. Attempting fallback with department-based search.")
                
                # 1. 질병명 추출 (이미 custom_tool_node 시작 부분에서 추출된 entities 사용)
                entities = await extract_entities_for_routing(llm_for_summary, state) # 재호출하여 최신 상태 반영
                loc = entities.get('location') # UnboundLocalError 방지를 위해 loc 변수 재할당

                dis = entities.get('disease')
                dep = entities.get('department') # 기존 추출된 department도 활용
                target = entities.get('target', '의사')
                
                logger.info(f"Tool {effective_tool_name} returned empty. Attempting fallback with department-based search.")
                if dis: # <-- 조건 변경
                    
                    inferred_depts = [] # inferred_depts를 미리 초기화합니다.
                    
                    if dep: # 기존에 추출된 department가 있다면 우선 사용
                        inferred_depts.append(dep)
                        logger.info(f"Using pre-extracted department for fallback: '{dep}'")
                    
                    if not inferred_depts and dis: # department가 없고, disease가 있다면 LLM으로 추론 시도
                        dept_inference_prompt = f"질병 '{dis}'에 대해 일반적으로 진료하는 진료과목을 2-3가지 정도 JSON 배열 형식으로만 알려줘. 다른 설명은 필요 없어. (예: ['감염내과', '가정의학과'])"
                        
                        try:
                            llm_response = await llm_for_summary.ainvoke(dept_inference_prompt)
                            json_match = re.search(r"```json\n(.*?)\n```", llm_response.content, re.DOTALL)
                            if json_match:
                                json_str = json_match.group(1)
                                inferred_depts_raw = json.loads(json_str)
                            else:
                                inferred_depts_raw = json.loads(llm_response.content)
                                
                            if isinstance(inferred_depts_raw, list):
                                inferred_depts.extend(inferred_depts_raw)
                            elif isinstance(inferred_depts_raw, str):
                                inferred_depts.append(inferred_depts_raw)
                            logger.info(f"Inferred departments for '{dis}': {inferred_depts}")

                        except (json.JSONDecodeError, ValueError) as e:
                            logger.error(f"Failed to infer departments for disease '{dis}': {e}", exc_info=True)
                    
                    if inferred_depts: # 진료과목이 추론되었거나 기존 dep에서 확보되었다면 라우팅 시작
                        new_observation = None
                        fallback_tool_key = None
                        fallback_params = {}

                        # 위치 정보 확인
                        has_location_info = (latitude is not None and longitude is not None) or (loc and loc.strip())
                        
                        if has_location_info:
                            # 지역 정보 있음 (의사/병원 검색)
                            if target == '의사':
                                fallback_tool_key = 'search_doctors_by_location_and_department'
                                fallback_params = {
                                    'department': inferred_depts,
                                    'is_location_near': is_proximity_query,
                                    'latitude': latitude,
                                    'longitude': longitude,
                                    'location': loc,
                                    'proposal' : llm_proposal or ""
                                }
                            else: # target == '병원'
                                fallback_tool_key = 'search_hospitals_by_location_and_department'
                                fallback_params = {
                                    'department': inferred_depts,
                                    'is_location_near': is_proximity_query,
                                    'latitude': latitude,
                                    'longitude': longitude,
                                    'location': loc
                                }
                        else:
                            # 지역 정보 없음 (의사/병원 검색)
                            if target == '의사':
                                fallback_tool_key = 'search_doctors_by_department_only'
                                fallback_params = {
                                    'department': inferred_depts,
                                    'proposal' : llm_proposal or ""
                                }
                            else: # target == '병원'
                                fallback_tool_key = 'recommend_hospital'
                                fallback_params = {
                                    'department': inferred_depts
                                }
                       
                        if fallback_tool_key:
                            try:
                                cleaned_fallback_params = {k: v for k, v in fallback_params.items() if v is not None}
                                # department 인자가 리스트인 경우 첫 번째 요소만 사용하도록 수정
                                if (fallback_tool_key == 'search_hospitals_by_location_and_department' or
                                    fallback_tool_key == 'search_doctors_by_location_and_department') and \
                                   isinstance(cleaned_fallback_params.get('department'), list) and \
                                   len(cleaned_fallback_params['department']) > 0:
                                    cleaned_fallback_params['department'] = cleaned_fallback_params['department'][0]
                                elif fallback_tool_key == 'recommend_hospital' and isinstance(cleaned_fallback_params.get('department'), str):
                                    cleaned_fallback_params['department'] = [cleaned_fallback_params['department']]

                                new_observation = None
                                try:
                                    if fallback_tool_key == 'search_doctors_by_location_and_department':
                                        cleaned_fallback_params['proposal'] = llm_proposal or ""
                                        new_observation = await search_doctors_by_location_and_department.ainvoke(cleaned_fallback_params)
                                    elif fallback_tool_key == 'search_hospitals_by_location_and_department':
                                        new_observation = await search_hospitals_by_location_and_department.ainvoke(cleaned_fallback_params)
                                    elif fallback_tool_key == 'search_doctors_by_department_only':
                                        cleaned_fallback_params['proposal'] = llm_proposal or ""
                                        new_observation = await search_doctors_by_department_only.ainvoke(cleaned_fallback_params)
                                    elif fallback_tool_key == 'recommend_hospital':
                                        new_observation = await recommend_hospital.ainvoke(cleaned_fallback_params)
                                    else:
                                        # 예외 처리: 예상치 못한 fallback_tool_key가 발생했을 경우
                                        raise ValueError(f"Unexpected fallback tool key: {fallback_tool_key}")
                                except Exception as tool_e:
                                    logger.info(f"[Fallback Logic] Error during invocation of fallback tool '{fallback_tool_key}' with params {cleaned_fallback_params}: {tool_e}", exc_info=True)
                                    new_observation = {"chat_type": "general", "answer": f"도구 실행 중 오류 발생: {str(tool_e)}"}

                                if new_observation is None or 'answer' not in new_observation or is_result_empty(fallback_tool_key, new_observation):
                                    logger.info(f"[Fallback Logic] Second fallback also returned empty. Returning generic info message.")
                                    observation = {"chat_type": "general", "answer": "아쉽게도 요청하신 정보는 현재 확인이 어렵습니다. 다른 방식으로 질문해주시거나, 좀 더 구체적인 정보(예: 병원 이름, 진료 과목)를 알려주시면 자세히 찾아보겠습니다."}
                                else:
                                    logger.info(f"[Fallback Logic] Second fallback returned results. Using new_observation.")
                                    observation = new_observation

                            except Exception as e:
                                logger.info(f"[Fallback except Exception else] 865행 ")
                                observation = {"chat_type": "general", "answer": f"도구 폴백 실행 중 오류 발생: {str(e)}"}
                        else:
                            logger.info(f"[Fallback except Exception else] 868행 ")
                            observation = {"chat_type": "general", "answer": "아쉽게도 요청하신 정보는 현재 확인이 어렵습니다. 다른 방식으로 질문해주시거나, 좀 더 구체적인 정보(예: 병원 이름, 진료 과목)를 알려주시면 자세히 찾아보겠습니다."}
                    else:
                        # 진료과목 추론에 실패하거나 dep도 없는 경우
                        logger.info(f"[Fallback except Exception else] 872행 ")
                        observation = {"chat_type": "info", "answer": "아쉽게도 요청하신 정보는 현재 확인이 어렵습니다. 다른 방식으로 질문해주시거나, 좀 더 구체적인 정보(예: 병원 이름, 진료 과목)를 알려주시면 자세히 찾아보겠습니다."}

                else: # (dis or dep) 조건에 해당하지 않는 경우
                    if not observation.get('answer'):
                        logger.info(f"[Fallback except Exception else] 877행 ")
                        observation = {"chat_type": "general", "answer": "아쉽게도 요청하신 정보는 현재 확인이 어렵습니다. 다른 방식으로 질문해주시거나, 좀 더 구체적인 정보(예: 병원 이름, 진료 과목)를 알려주시면 자세히 찾아보겠습니다."}

        
        # 🚨 이 부분에서 observation이 None이 되는 것을 방지하고 항상 dict 형태로 만듦
        if observation is None:
            observation = {"chat_type": "general", "answer": f"Tool {tool_name} not found or failed to execute, but no specific error message was captured."}
        elif not isinstance(observation, dict):
            # observation이 문자열 등의 dict가 아닌 경우를 대비하여 딕셔너리로 래핑
            observation = {"chat_type": "general", "answer": str(observation)}

        tool_messages.append(
            ToolMessage(content=json.dumps(observation, ensure_ascii=False), tool_call_id=tool_call["id"])
        )
        
    return {"messages": tool_messages}

# 4️⃣ 재시도 분기 처리 함수
def should_retry(state: AgentState) -> Literal["agent", END]:
    """검증이후에 분기하는 edge"""
    if state["valid"]:
        return END
    else:
        logger.info(f"Retry attempt {state.get('retry', 0)}")
        return "agent"
    
# 그래프를 생성하고 컴파일하는 함수
async def get_compiled_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", custom_tool_node)
    workflow.add_node("validate", validate_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_conditional_edges("validate", should_retry, {"agent": "agent", END: END})

    conn = await aiosqlite.connect(settings.sqlite_directory, check_same_thread=False)
    
    # 🚨 Add this block to create tool_results_cache table if it doesn't exist
    async with conn.cursor() as cursor:
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_results_cache (
                session_id TEXT NOT NULL,
                result_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        await conn.commit()
    # 🚨 End of new block
    
    memory = AsyncSqliteSaver(conn=conn)

    graph = workflow.compile(checkpointer=memory)
    return graph