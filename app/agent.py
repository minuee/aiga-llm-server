import os
import asyncio
import json
from langchain_openai import AzureOpenAI, AzureChatOpenAI
from langgraph.graph import StateGraph, END, START
from typing import TypedDict, Annotated, Literal
from .config import settings
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import MessagesState, add_messages
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage

from .tools.tools import recommend_doctor, recommend_hospital, search_doctor, search_doctor_by_hospital, search_doctor_for_else_question
from .prompt.system_prompt import SYSTEM_PROMPT
from .prompt.validation_prompt import VALIDATION_PROMPT
from .common.logger import logger

import aiosqlite
import redis.asyncio as redis

# 모델 설정
llm = AzureChatOpenAI(
    model_name=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0
)

llm_for_summary = AzureChatOpenAI(
    model_name=settings.azure_summary_api_model, # TODO: Use a cheaper model for summarization
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0
)

tools = [recommend_doctor, recommend_hospital, search_doctor, search_doctor_by_hospital, search_doctor_for_else_question]
model = llm.bind_tools(tools)

redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

def summarize_messages_for_cache_key(messages):
    summary = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            summary.append(f"S:{msg.content}")
        elif isinstance(msg, HumanMessage):
            summary.append(f"H:{msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                tool_calls_str = ",".join(f"T:{tc['name']}({tc['args']})" for tc in msg.tool_calls)
                summary.append(f"A:{msg.content}[{tool_calls_str}]")
            else:
                summary.append(f"A:{msg.content}")
        elif isinstance(msg, ToolMessage):
            summary.append(f"ToolResult({msg.tool_call_id}):{msg.content}")
    return "\n".join(summary)

class AgentState(MessagesState):
    messages: Annotated[list, add_messages]
    retry: Annotated[int, 0]
    valid: Annotated[bool, False]
    summary_input_tokens: Annotated[int, 0]
    summary_output_tokens: Annotated[int, 0]
    summary_total_tokens: Annotated[int, 0]

# 1️⃣ 에이전트 노드
async def agent_node(state: AgentState):
    """모델을 통해 답변하는 Agent 노드. 대화가 길어지면 요약 기능이 동작합니다."""

    # --- START: 대화 요약 로직 ---
    messages = state["messages"]
    
    # 요약 토큰 정보를 담을 변수 초기화
    s_input_tokens, s_output_tokens, s_total_tokens = 0, 0, 0

    # 시스템 메시지가 없는 경우 추가하고, 원본 시스템 메시지 저장
    if not any(isinstance(msg, SystemMessage) for msg in messages):
        system_message = SystemMessage(content=SYSTEM_PROMPT)
        messages.insert(0, system_message)
        original_system_message = system_message
    else:
        original_system_message = messages[0]

    # 요약 설정값
    CHAR_THRESHOLD = 5000  # 이 '글자 수'를 넘으면 요약 실행
    MESSAGES_TO_KEEP_IF_NO_TOOL = 4 # Tool 없을 때 남길 최근 메시지 수

    # 문자 수 기반으로 토큰 수 근사치 계산
    approx_tokens = sum(len(m.content) for m in messages if hasattr(m, 'content'))
    logger.info(f"DEBUG: Calculated approx_tokens (character count) = {approx_tokens}") # Debug log

    if approx_tokens > CHAR_THRESHOLD:
        logger.info(f"Character count ({approx_tokens}) exceeds threshold ({CHAR_THRESHOLD}). Starting summarization process.")

        # --- START: 'Descriptive Timeline Summary' Logic ---

        # 1. 기존 요약과 새로운 대화를 분리
        last_summary_index = -1
        for i, msg in reversed(list(enumerate(messages))):
            if isinstance(msg, HumanMessage) and msg.content.startswith("이전 대화는 다음과 같이 요약되었습니다:"):
                last_summary_index = i
                break
        
        old_summary_text = ""
        if last_summary_index != -1:
            old_summary_text = messages[last_summary_index].content.replace("이전 대화는 다음과 같이 요약되었습니다:\n", "")
            messages_since_last_summary = messages[last_summary_index + 1:]
        else:
            messages_since_last_summary = messages[1:]

        # 2. '새로운 대화' 내에서 요약할 부분과 보존할 부분 결정
        last_tool_message_index = -1
        for i, msg in reversed(list(enumerate(messages_since_last_summary))):
            if isinstance(msg, ToolMessage):
                last_tool_message_index = i
                break

        messages_to_summarize = []
        messages_to_keep = []
        
        if last_tool_message_index != -1:
            if last_tool_message_index > 0 and isinstance(messages_since_last_summary[last_tool_message_index - 1], AIMessage) and messages_since_last_summary[last_tool_message_index - 1].tool_calls:
                messages_to_summarize = messages_since_last_summary[:last_tool_message_index - 1]
                messages_to_keep = messages_since_last_summary[last_tool_message_index - 1:]
            else:
                messages_to_summarize = messages_since_last_summary[:last_tool_message_index]
                messages_to_keep = messages_since_last_summary[last_tool_message_index:]
        else:
            if len(messages_since_last_summary) > MESSAGES_TO_KEEP_IF_NO_TOOL:
                messages_to_summarize = messages_since_last_summary[:-MESSAGES_TO_KEEP_IF_NO_TOOL]
                messages_to_keep = messages_since_last_summary[-MESSAGES_TO_KEEP_IF_NO_TOOL:]
            else:
                messages_to_keep = messages_since_last_summary

        # 3. 요약이 필요한 경우, '서술형 타임라인' 형식으로 요약 조각 생성
        if messages_to_summarize:
            # 3a. ToolMessage 내용 사전 처리
            texts_for_summary = []
            for m in messages_to_summarize:
                if isinstance(m, HumanMessage):
                    texts_for_summary.append(f"사용자: {m.content}")
                elif isinstance(m, AIMessage):
                    if m.tool_calls:
                        try:
                            tool_name = m.tool_calls[0]['name']
                            texts_for_summary.append(f"AI: ({tool_name} 도구 사용) {m.content}")
                        except (IndexError, KeyError):
                            texts_for_summary.append(f"AI: (도구 사용) {m.content}")
                    else:
                        texts_for_summary.append(f"AI: {m.content}")
                elif isinstance(m, ToolMessage):
                    try:
                        tool_data = json.loads(m.content)
                        summary_of_tool = "도구 결과: "
                        data_list = tool_data.get('answer', {}).get('doctors', []) or tool_data.get('answer', {}).get('hospitals', [])
                        if data_list:
                            summaries = [f"[{item.get('name', '')}/{item.get('hospital', '')}/{item.get('deptname', '')}]" for item in data_list]
                            summary_of_tool += ", ".join(summaries)
                        elif 'answer' in tool_data and isinstance(tool_data['answer'], str):
                            summary_of_tool += f"[{tool_data['answer']}]"
                        else:
                            summary_of_tool += "[데이터]"
                        texts_for_summary.append(summary_of_tool)
                    except (json.JSONDecodeError, TypeError, KeyError):
                        # If JSON parsing fails, treat content as a raw string (likely an error message)
                        texts_for_summary.append(f"[도구 오류: {m.content}]")
            
            new_conversation_text = "\n".join(texts_for_summary)

            # 3b. 서술형 타임라인 요약 프롬프트 사용
            summary_prompt_text = (
                f"다음 대화 내용을 하나의 완결된 문장으로, 서술형으로 요약해주세요.\n"
                f"요약에는 대화의 핵심 의도와 맥락이 잘 드러나야 합니다.\n"
                f"만약 대화 내용에 특정 의사, 병원, 질환명 등 핵심 개체가 포함되어 있다면, 요약문에 해당 개체들을 명시적으로 포함해주세요.\n"
                f"예: '사용자의 OOO 요청에 따라, AI가 XXX 도구를 사용해 YYY 정보를 제공함. (포함된 개체: 김의사, 서울병원)'\n\n"
                f"--- 요약할 대화 내용 ---\n{new_conversation_text}\n\n"
                f"--- 서술형 요약 ---"
            )

            summary_response = await llm_for_summary.ainvoke([HumanMessage(content=summary_prompt_text)])
            new_summary_snippet = summary_response.content.strip()
            
            if hasattr(summary_response, 'usage_metadata') and summary_response.usage_metadata:
                summary_tokens = summary_response.usage_metadata
                s_input_tokens = summary_tokens.get('prompt_tokens', 0)
                s_output_tokens = summary_tokens.get('completion_tokens', 0)
                s_total_tokens = summary_tokens.get('total_tokens', 0)
            
            # 4. 새로운 타임라인 이벤트를 기존 요약에 추가
            final_summary_text = old_summary_text
            if new_summary_snippet:
                if final_summary_text:
                    final_summary_text += "\n" + new_summary_snippet
                else:
                    final_summary_text = new_summary_snippet

            # 5. 새로운 메시지 목록으로 재구성
            messages = [
                original_system_message,
                HumanMessage(content=f"이전 대화는 다음과 같이 요약되었습니다:\n{final_summary_text}"),
                *messages_to_keep
            ]
            state["messages"] = messages

    # --- END: 대화 요약 로직 ---

    # 캐시 및 모델 호출 (요약이 적용된 messages 사용)
    cache_key = f"chat:{summarize_messages_for_cache_key(messages)}"
    cached_content = await redis_client.get(cache_key)
    if cached_content:
        logger.info(f"Cache hit!")
        return {"messages": [AIMessage(content=cached_content)], "retry": state.get("retry", 0)}

    logger.info("Cache miss. Calling model...")
    response = await model.ainvoke(messages)
    await redis_client.set(cache_key, response.content, ex=3600)
    
    retry = state.get("retry", 0)
    
    return {
        "messages": [response], 
        "retry": retry,
        "summary_input_tokens": state.get("summary_input_tokens", 0) + s_input_tokens,
        "summary_output_tokens": state.get("summary_output_tokens", 0) + s_output_tokens,
        "summary_total_tokens": state.get("summary_total_tokens", 0) + s_total_tokens,
    }



# 2️⃣ 분기 엣지
def should_continue(state: AgentState):
    """모델 답변에 따라 분기 처리하는 edge"""
    if state["messages"][-1].tool_calls:
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

# 4️⃣ 재시도 분기 처리 함수
def should_retry(state: AgentState) -> Literal["agent", END]:
    """검증이후에 분기하는 edge"""
    if state["valid"]:
        return END
    else:
        logger.warning(f"Retry attempt {state.get('retry', 0)}")
        return "agent"
    
# 그래프를 생성하고 컴파일하는 함수
async def get_compiled_graph():
    workflow = StateGraph(AgentState)

    tool_node = ToolNode(tools=tools)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("validate", validate_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_conditional_edges("validate", should_retry, {"agent": "agent", END: END})

    conn = await aiosqlite.connect(settings.sqlite_directory, check_same_thread=False)
    memory = AsyncSqliteSaver(conn=conn)

    graph = workflow.compile(checkpointer=memory)
    return graph
