import re
import os
import json # 🚨 Add json for parsing content
import aiosqlite # 🚨 Add aiosqlite for accessing the SQLite DB
import asyncio
from typing import Optional, Union, List
from langchain_core.caches import RETURN_VAL_TYPE
from langchain_core.tools import tool, BaseTool
from langchain_core.prompts import ChatPromptTemplate # <--- Inject방지 
from .standard_desease_dic import STANDARD_DESEASE_DIC
from .location_dic import LOCATION_NORMALIZATION_RULES, GROUP_LOCATION_EXPANSION_RULES
from ..common.location_analyzer import classify_location_query
from ..database.standardSpecialty import getStandardSpecialty as getStandardSpecialtyFromDB
from ..database.recommandDoctors import getRecommandDoctors
from ..database.doctor_paper import getDoctorPaper, getPatientMaxScore
from ..database.recommandHospital import getRecommandHospitals
from ..common.common import getValidHospitalName
from ..database.searchDoctor import getSearchDoctors, getSearchDoctorsByHospitalAndDept, getSearchDoctorsByOnlyHospital
from ..database.db import engine as db_engine
from ..common.logger import logger

from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr
from sqlalchemy import text

# LangChain LLM 캐시를 위한 모듈 임포트
from langchain.globals import set_llm_cache
from langchain_community.cache import SQLiteCache

from ..config import settings
from ..prompt.sql_agent_prompt import FILE_SQL_AGENT_PROMPT_JSON_DISABLED,FILE_SQL_AGENT_PROMPT_JSON_ENABLED
LIMIT_RECOMMAND_DOCTOR = int(os.environ.get('LIMIT_RECOMMAND_DOCTOR', '10'))
LIMIT_RECOMMAND_PAPER = int(os.environ.get('LIMIT_RECOMMAND_PAPER', '10'))
LIMIT_RECOMMAND_HOSPITAL = int(os.environ.get('LIMIT_RECOMMAND_HOSPITAL', '10'))

# --- LangChain LLM 캐시 설정 시작 ---
if settings.llm_sql_agent_cache_verbose:
    # 캐시 적중(hit) 시 로그를 남기기 위해 SQLiteCache를 상속받는 커스텀 클래스 정의
    class LoggingSQLiteCache(SQLiteCache):
        """캐시 조회 시 성공/실패 모두 로그를 남기는 SQLiteCache"""
        _miss_count = 0  # MISS 횟수를 기록하기 위한 클래스 변수

        def lookup(self, prompt: str, llm_string: str) -> Optional[RETURN_VAL_TYPE]:
            cached_val = super().lookup(prompt, llm_string)
            if cached_val is not None:
                logger.info(f"LLM Cache HIT! (prompt: {prompt[:100]}...)")
            else:
                logger.info(f"LLM Cache MISS! (prompt: {prompt[:100]}...)")
            return cached_val

    # settings.cache_sqlite_directory를 사용하여 캐시 파일 경로 설정
    cache_db_path = os.path.join(settings.cache_sqlite_directory, "llm_sqlite_cache.db")
    # SQLite를 사용하여 LLM 응답을 캐싱하도록 전역 설정. 이때 커스텀 클래스인 LoggingSQLiteCache를 사용.
    set_llm_cache(LoggingSQLiteCache(database_path=cache_db_path))
    logger.info("LLM SQL Agent Cache 기능 활성화됨.")
else:
    set_llm_cache(None) # 캐시 비활성화 시 전역 캐시를 None으로 설정
    logger.info("LLM SQL Agent Cache 기능 비활성화됨.")
# --- LangChain LLM 캐시 설정 종료 ---

# SQL agent를 위한 별도 설정
sql_llm = AzureChatOpenAI(
    azure_deployment=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=SecretStr(settings.azure_key),
    api_version=settings.azure_api_version,
    temperature=0
)

# SQLDatabase 인스턴스 생성 (db_engine 활용하여 커넥션 풀 통합)
sql_db = SQLDatabase(
    db_engine,
    sample_rows_in_table_info=0,
    include_tables=['hospital', 'hospital_evaluation', 'doctor', 'doctor_basic', 'doctor_career', 'doctor_evaluation']
)

from .sql_tool import search_hospitals_by_location_and_department, search_doctor_details_by_name, search_hospital_details_by_name, search_doctors_by_location_and_department, search_doctors_by_disease_and_location, search_hospital_by_disease_and_location, search_doctors_by_hospital_name, search_hospital_by_disease, search_hospital_by_disease_and_department, _get_coords_for_location


# SQL agent 생성
sql_agent_executor = create_sql_agent(
    sql_llm, 
    db=sql_db, 
    agent_type="openai-tools", 
    verbose=settings.sql_agent_verbose,
    top_k=5,
    max_iterations=7
)


def getStandardDeseaseDictionary(disease: str):
    """표준 진료 분야 사전을 통해서 진료 분야를 구하는 함수"""

    # 1차: 유사어 딕셔너리 처리
    logger.debug("1차: 유사어 딕셔너리 처리")
    standard_disease = ''
    for key, values in STANDARD_DESEASE_DIC.items():
        if disease in values:
            standard_disease = key
            break
    if standard_disease:
        return standard_disease
    
async def getStandardSpecialty(disease: str):
    """표준 진료 분야를 구하는 함수"""

    # 1차: 유사어 딕셔너리 처리: 각막질환
    standardSpecialty = getStandardDeseaseDictionary(disease)
    if standardSpecialty:
        return standardSpecialty

    # 2차: 표준진료분야 선택: 각막이식
    standardSpecialty = await asyncio.to_thread(getStandardSpecialtyFromDB, disease)
    if standardSpecialty:
        return standardSpecialty

    return None

def formattingDoctorInfo(doctors, isEntire=False):
    """의사 정보를 포맷팅하는 함수"""     
    doctorList = []
    added_doctor_ids = set()  # 이미 추가된 doctor_id를 추적

    for doctor in doctors:

        # 중복 제거
        if doctor['doctor_id'] in added_doctor_ids:
            continue

        added_doctor_ids.add(doctor['doctor_id'])

        doctor = {
            "doctor_id": doctor.get('doctor_id'),
            "doctor_rid": doctor.get('hexrid', ''),
            "hospital": doctor.get('shortname', ''),
            "hospital_hid" : doctor.get('hid',''),
            "address": doctor.get('address', ''),
            "lat": doctor.get('lat'),
            "lon": doctor.get('lon'),
            "telephone": doctor.get('telephone', ''),
            "hospital_site" : doctor.get('hospital_site',''),
            "name": doctor.get('doctorname', ''),
            "deptname": doctor.get('deptname', ''),
            "specialties": doctor.get('specialties', ''),
            "parse_specialties": doctor.get('parse_specialties', ''),
            "url": doctor.get('doctor_url', ''),
            "education": doctor.get('education', ''),
            "career": doctor.get('career', ''),
            "paper": [], #tritease[:LIMIT_RECOMMAND_PAPER],
            "photo": doctor.get('profileimgurl', ''),
            "doctor_score": {
                "paper_score": doctor.get('paper_score', 0.0),
                "patient_score": doctor.get('patient_score', 0.0),
                "public_score": doctor.get('public_score', 0.0),
                "peer_score": 0.0
            },
            "ai_score": {
                "kindness": 0.0,
                "satisfaction": 0.0,
                "explanation": 0.0,
                "recommendation": 0.0
            },
            "review": []
        }
        doctorList.append(doctor)

    return doctorList




async def _get_coords_for_location_old(location_name: str):
    """
    DB 조회를 통해 특정 지역명의 평균 좌표를 계산하는 내부 헬퍼 함수.
    "수도권" 같은 그룹 지역명도 처리할 수 있다.
    """
    from sqlalchemy import text
    import asyncio
    import re

    params = {}
    where_clause = ""

    if location_name in GROUP_LOCATION_EXPANSION_RULES:
        expansion = GROUP_LOCATION_EXPANSION_RULES[location_name]
        sub_locations = [loc for loc in re.findall(r'(\w+)', expansion) if loc.lower() not in ['또는', 'or']]
        
        if sub_locations:
            where_clause = " OR ".join([f"address LIKE '%%{loc}%%'" for loc in sub_locations])
            where_clause = f"({where_clause})"
        else:
            where_clause = "address LIKE :location"
            params = {"location": f"%%{location_name}%%"}
    
    else:
        where_clause = "address LIKE :location"
        params = {"location": f"%%{location_name}%%"}

    query_str = f"SELECT AVG(lat) as lat, AVG(lon) as lon FROM hospital WHERE {where_clause} AND lat IS NOT NULL AND lon IS NOT NULL AND hid LIKE 'H01KR%'"
    query = text(query_str)
    
    try:
        def _execute_query():
            with db_engine.connect() as connection:
                return connection.execute(query, params).fetchone()

        row = await asyncio.to_thread(_execute_query)
        
        if row and row[0] is not None and row[1] is not None:
            coords = {'lat': float(row[0]), 'lon': float(row[1])}
            logger.info(f"내부 Geocoding 성공: '{location_name}' -> 좌표: {coords}")
            return coords
    except Exception as e:
        logger.error(f"내부 Geocoding 중 오류 발생 '{location_name}': {e}")
    
    return None

@tool
async def recommand_doctor(disease: Union[str, List[str]], limit: int = None, latitude: float = None, longitude: float = None, logical_operator: str = 'OR') -> dict:
    """질환명(disease) 기반 의사 추천 도구. 여러 질환명을 입력받을 수 있습니다."""
    
    disease_list = []
    if isinstance(disease, str):
        # "협심증과 녹내장", "협심증,녹내장" 등 다양한 구분자 처리
        disease_list = [d.strip() for d in re.split(r'[\\s,과및/]+', disease) if d.strip()]
    elif isinstance(disease, list):
        disease_list = [d.strip() for d in disease if d.strip()]

    if not disease_list:
        raise ValueError("질환명은 필수 입력값입니다.")

    logger.info(f"tool:recommand_doctor, diseases:{disease_list}, limit:{limit}, operator:{logical_operator}")

    # 비동기적으로 표준 질환명 조회
    standard_diseases_tasks = [getStandardSpecialty(d) for d in disease_list]
    standard_diseases_results = await asyncio.gather(*standard_diseases_tasks)
    
    # None 값을 제외하고 유니크한 표준 질환명 리스트 생성
    standard_diseases = sorted(list(set(d for d in standard_diseases_results if d)))
    original_diseases = sorted(list(set(disease_list)))
    
    logger.info(f"Original diseases: {original_diseases}, Standardized diseases: {standard_diseases}")
    
    # getRecommandDoctors 호출 시 logical_operator 전달
    doctors = getRecommandDoctors(
        standard_disease=standard_diseases, 
        disease=original_diseases,
        logical_operator=logical_operator
    )

    formattedDoctors = formattingDoctorInfo(doctors["data"])
    
    result = {
        "chat_type": "recommand_doctor",
        "answer": {
            "disease": ", ".join(standard_diseases if standard_diseases else original_diseases),
            "hospital": "",
            "doctors": [],
        }
    }

    if limit is not None and limit > 0:
        result_limit = limit
    else:
        result_limit = LIMIT_RECOMMAND_DOCTOR

    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:result_limit]
        
    return result

@tool
async def recommend_hospital(department: Union[str, List[str]], limit: int = None, latitude: float = None, longitude: float = None, is_nearby: bool = False) -> dict:
    """하나 또는 여러 진료과(department) 기반 병원 추천 도구. is_nearby가 True이면 거리순으로, False이면 평가순으로 결과를 정렬합니다."""
    
    dept_list = []
    if isinstance(department, str):
        # "가정의학과와 재활의학과", "가정의학과,재활의학과" 등 다양한 구분자 처리
        dept_list = [d.strip() for d in re.split(r'[\\s,과및/]+', department) if d.strip()]
    elif isinstance(department, list):
        dept_list = [d.strip() for d in department if d.strip()]

    if not dept_list:
        raise ValueError("진료과명은 필수 입력값입니다.")

    logger.info(f"tool:recommend_hospital 시작 , departments:{dept_list}, limit:{limit}, latitude:{latitude}, longitude:{longitude}, is_nearby:{is_nearby}")
    
    if limit is not None and limit > 0:
        result_limit = limit
    else:
        result_limit = LIMIT_RECOMMAND_HOSPITAL
        
    logger.info("Calling database function: getRecommandHospitals from app/database/recommandHospital.py")
    hospitals = getRecommandHospitals(dept_list, result_limit, latitude, longitude, is_nearby)
    
    result = {
        "chat_type": "recommand_hospital",
        "answer": {
            "department": ", ".join(dept_list),
            "hospitals": [],
        }
    }
    if hospitals:
        result["answer"]["hospitals"] = hospitals[:result_limit]
    return result

@tool
def search_doctor(name: str, hospital:str = "", deptname:str = "", proposal: str = "", limit: int = None) -> dict:
    """의사이름(name) 기반 의사 검색 도구"""
    name = name.replace(" ", "").strip()
    hospital = hospital.replace(" ", "").strip()
    deptname = deptname.replace(" ", "").strip()
    logger.info(f"tool:search_doctor 시작 : name:{name}, hospital:{hospital}, deptname:{deptname}, limit:{limit}")
    if not name:
        raise ValueError("의사명은 필수 입력값입니다.")
    if hospital:
        hospital = getValidHospitalName(hospital)
    doctors = None
    if hospital and deptname:
        doctors = getSearchDoctors(name, hospital, deptname)
        if len(doctors) < 1:
            doctors = getSearchDoctors(name, hospital)
            if len(doctors) < 1:
                doctors = getSearchDoctors(name)
    elif hospital:
        doctors = getSearchDoctors(name, hospital)
        if len(doctors) < 1:
            doctors = getSearchDoctors(name)
    else:
        doctors = getSearchDoctors(name)
    formattedDoctors = formattingDoctorInfo(doctors)
    result = {
        "chat_type": "search_doctor",
        "answer": {
            "name": name,
            "hospital": hospital,
            "deptname": deptname,
            "doctors": [],
            "proposal": proposal,
        }
    }
    if limit is not None and limit > 0:
        result_limit = limit
    else:
        result_limit = LIMIT_RECOMMAND_DOCTOR
    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:result_limit]
    return result

@tool
def search_doctor_by_hospital(hospital: str, deptname: Union[str, List[str]] = "", proposal: str = "", limit: int = None) -> dict:
    """병원과 하나 또는 여러 진료과 기반 의사 검색 도구"""
    logger.info(f"tool:search_doctor_by_hospital 시작 1 : hospital:{hospital}, deptname:{deptname}, proposal : {proposal}, imit:{limit}")
    hospital = hospital.replace(" ", "").strip()

    # deptname 처리: 문자열이면 공백 제거, 리스트면 각 항목 공백 제거 및 빈 항목 필터링
    if isinstance(deptname, str):
        deptname = deptname.strip()
    elif isinstance(deptname, list):
        deptname = [d.strip() for d in deptname if d.strip()]

    logger.info(f"tool:search_doctor_by_hospital 시작 2 : hospital:{hospital}, deptname:{deptname}, proposal : {proposal}, imit:{limit}")
    if not hospital:
        raise ValueError("병원명은 필수 입력값입니다.")

    hospital = getValidHospitalName(hospital)
    
    # deptname이 비어있지 않은 경우(빈 문자열이 아니거나, 비어있지 않은 리스트)에만 진료과로 검색
    if deptname:
        doctors = getSearchDoctorsByHospitalAndDept(hospital, deptname)
    else:
        doctors = getSearchDoctorsByOnlyHospital(hospital)

    formattedDoctors = formattingDoctorInfo(doctors)
    result = {
        "chat_type": "search_doctor",
        "answer": {
            "name": "",
            "hospital": hospital,
            "deptname": deptname,
            "doctors": [],
            "proposal": proposal
        }
    }
    if limit is not None and limit > 0:
        result_limit = limit
    else:
        result_limit = LIMIT_RECOMMAND_DOCTOR
    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:result_limit]
    return result

# SQL Agent를 위한 프롬프트 템플릿 정의 (모듈 레벨에서 한 번만 생성)
SQL_AGENT_PROMPT_JSON_DISABLED = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            FILE_SQL_AGENT_PROMPT_JSON_DISABLED
        ),
    ("human", "{question}"), # 사용자의 질문은 여기에 삽입됩니다.
    ]
)
SQL_AGENT_PROMPT_JSON_ENABLED = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            FILE_SQL_AGENT_PROMPT_JSON_ENABLED
        ),
    ("human", "{question}"), # 사용자의 질문은 여기에 삽입됩니다.
    ]
)


@tool
async def get_cached_tool_result(result_id: str) -> dict:
    """
    과거 ToolMessage의 상세 결과를 조회하는 도구.
    agent_node에서 대용량 ToolMessage content를 SQLite에 저장하고, 
    그 대신 플레이스홀더를 대화 기록에 남겼을 때 사용한다.
    
    Args:
        result_id: 필수 - SQLite에 저장된 도구 결과의 고유 ID (uuid)
    """
    ## logger.info(f"tool: get_cached_tool_result 시작 - result_id: {result_id}")
    conn = None  # finally 블록에서 접근할 수 있도록 바깥에 정의
    try:
        conn = await aiosqlite.connect(settings.sqlite_directory, check_same_thread=False)
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT content FROM tool_results_cache WHERE result_id = ?", 
                (result_id,)
            )
            row = await cursor.fetchone()

        if row:
            content = row[0]
            return json.loads(content)
        else:
            logger.warning(f"Cached tool result not found for result_id: {result_id}")
            return {"status": "error", "message": f"Cached tool result not found for result_id: {result_id}"}
            
    except Exception as e:
        logger.error(f"Error in get_cached_tool_result for result_id {result_id}: {e}", exc_info=True)
        # 에러가 발생하더라도 finally 블록을 통해 연결은 닫힘
        return {"status": "error", "message": f"Error retrieving cached tool result: {str(e)}"}
    finally:
        if conn:
            await conn.close()
            logger.info(f"tool: get_cached_tool_result - SQLite connection closed for result_id: {result_id}")

def normalize_location_in_question(question: str) -> str:
    """
    Normalizes location names in the user's question for better search.
    Handles both grouped regions (e.g., "경상도") and single regions (e.g., "경상북도").
    """
    # Stage 1: Expand grouped regions into explicit OR clauses for the LLM
    for group_name, expansion in GROUP_LOCATION_EXPANSION_RULES.items():
        if group_name in question:
            question = question.replace(group_name, expansion)
            # Only replace the first group found to avoid nested replacements
            break
    
    # Stage 2: Normalize individual region names to their root word
    for full_name, root_word in LOCATION_NORMALIZATION_RULES:
        question = question.replace(full_name, root_word)
        
    return question


@tool
async def search_doctor_for_else_question(question: str, use_json_output: bool = False, latitude: Optional[float] = None, longitude: Optional[float] = None, proposal: str = "") -> dict:
    """그밖의 질문(question)에 대한 응답 도구 - SQL agent를 사용하여 테이블 정보를 기반으로 답변. 이 도구는 최종적으로 LLM이 답변을 요약하여 chat_type: 'general'로 반환합니다."""
    logger.info(f"tool: search_doctor_for_else_question 시작 - question: {question}, proposal : {proposal}")

    # 이 도구는 항상 LLM 요약을 통해 일반 답변을 생성하므로, SQL Agent가 구조화된 데이터를 반환하도록 유도합니다.
    use_json_output = True

    SUMMARIZER_PROMPT_TEMPLATE = '''당신은 친절한 의료 전문 어시스턴트입니다.
사용자가 다음과 같은 질문을 했습니다:
"{question}"

이 질문에 답변하기 위해, 데이터베이스에서 다음 정보를 찾았습니다:
"{data}"

이 데이터를 바탕으로, 사용자가 이해하기 쉬운 자연스러운 문장으로 답변을 생성해주세요.
- 단순히 데이터를 나열하지 말고, 대화 형식으로 친절하게 설명해주세요.
- 만약 데이터가 비어있거나 검색 결과가 없음을 나타내는 경우, 요청에 맞는 정보를 찾을 수 없었다고 사용자에게 부드럽게 알려주세요.
- 답변은 반드시 한국어로 작성해야 합니다.

최종 답변:
'''

    try:
        normalized_question = normalize_location_in_question(question)
        if question != normalized_question:
            logger.info(f"Location normalized question: {normalized_question}")

        augmented_question = normalized_question
        
        # --- NLP 기반 위치 쿼리 분석 (기존 로직 유지) ---
        classification, anchor_noun, is_nearby = classify_location_query(normalized_question)

        if classification == "NAMED_LOCATION" and anchor_noun and is_nearby:
            logger.info(f"근접 검색 (장소 기준): 언급된 지역명 '{anchor_noun}' 기준")
            coords = await _get_coords_for_location(anchor_noun)
            if coords:
                augmented_question += f"\\n\\n[참고 위치 정보: '{anchor_noun}'은(는) 위도={coords['lat']:.4f}, 경도={coords['lon']:.4f} 근처입니다.]"
        
        elif classification == "USER_LOCATION" and latitude is not None and longitude is not None:
            logger.info("근접 검색 (GPS 기준): 사용자 현재 위치를 기준으로 합니다.")
            augmented_question += f"\\n\\n[사용자 위치 정보: 위도={latitude}, 경도={longitude}.]"
        else:
            if latitude is not None and longitude is not None:
                augmented_question += f"\\n\\n[사용자 위치 정보: 위도={latitude}, 경도={longitude}.]"
            logger.info("근접 검색 의도가 없어, 위치 컨텍스트만 주입합니다.")
        # --- NLP 기반 위치 쿼리 분석 종료 ---

        # SQL Agent 호출 시 항상 JSON 출력을 요청하도록 설정
        final_input = FILE_SQL_AGENT_PROMPT_JSON_ENABLED.format(question=augmented_question)

        result = await sql_agent_executor.ainvoke({"input": final_input})
        
        output_str = result.get("output", "{}")
        
        # LLM이 markdown 코드 블록을 포함할 경우 제거
        if "```json" in output_str:
            match = re.search(r'```json\n(.*?)\n```', output_str, re.DOTALL)
            if match:
                output_str = match.group(1)

        # SQL Agent의 결과를 요약 LLM에 전달하여 최종 답변 생성
        summary_prompt = SUMMARIZER_PROMPT_TEMPLATE.format(
            question=question,
            data=output_str
        )
        
        summary_response = await sql_llm.ainvoke(summary_prompt)
        final_answer = summary_response.content
        
        logger.info("SQL Agent 결과를 요약하여 general 답변으로 반환합니다.")
        return {"chat_type": "general", "answer": final_answer}

    except Exception as e:
        logger.error(f"tool: search_doctor_for_else_question 에러 - {str(e)}")
        # 에러 발생 시에도 요약을 시도해 볼 수 있으나, 여기서는 일반적인 에러 메시지 반환
        return {
            "chat_type": "general",
            "answer": f"요청을 처리하는 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        }
