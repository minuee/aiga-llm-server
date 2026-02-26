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
            clarification_question = f"{greeting}

  {clarification_question}"
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
        location_gps_info = f"

/*
IMPORTANT: User's current GPS location is available.
Latitude: {current_latitude}
Longitude: {current_longitude}
Prioritize using location-based tools if the user asks for nearby facilities.
*/
"
    
    locale = state.get("locale") or "ko"
    language_name = LANGUAGE_SET.get(locale, LANGUAGE_SET["ko"])
    language_rule = f"

**Response Language Rule**
- The AI counselor's final response MUST be generated in **{language_name}**.
"

    # 2. 기본 SYSTEM_PROMPT에서 기존 GPS 정보 블록 및 기타 불필요한 정보 제거
    clean_system_prompt_base = re.sub(r'\[사용자 현재 위치 정보 \(GPS\)\].*?\[지역 사전 분석 플래그\]', '', SYSTEM_PROMPT, flags=re.DOTALL)
    clean_system_prompt_base = re.sub(r'
+/\*.*?IMPORTANT:.*?\*/
*', '', clean_system_prompt_base, flags=re.DOTALL)
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
                        if not isinstance(tool_content_json, dict) or tool_content_json.get("migrated") is True:
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
                logger.info(f"Detected {len(cache_calls)} cache restoration calls. Handling internally and re-invoking LLM.")
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
                                    logger.info(f"Successfully restored cache for result_id: {result_id}")
                                    tool_msg = ToolMessage(content=row[0], tool_call_id=tc['id'])
                                    loop_messages.append(tool_msg)
                                    intermediate_messages.append(tool_msg)
                                else:
                                    logger.warning(f"Cache not found for result_id: {result_id}")
                                    tool_msg = ToolMessage(content=json.dumps({"error": "Cache not found"}), tool_call_id=tc['id'])
                                    loop_messages.append(tool_msg)
                                    intermediate_messages.append(tool_msg)
                
                logger.info("Re-invoking model with restored cache context...")
                response = await model.ainvoke(loop_messages, config)
        # 🚨 [END] 내부 캐시 복원 루프

    except Exception as e:
        logger.warning(f"LLM call failed with error: {e}. Checking for content filter.")
        error_message = str(e)
        
        if "An assistant message with 'tool_calls' must be followed by tool messages" in error_message or 
           "tool_call_ids did not have response messages" in error_message or 
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
        response.content = f"{greeting}

 {response.content}"

    state["last_ai_message"] = response.content 

    return {
        "messages": intermediate_messages + [response], 
        "retry": state.get("retry", 0),
        "location_history": state['location_history'],
        "entity_history": state['entity_history'],
        "last_ai_message": state["last_ai_message"]
    }
