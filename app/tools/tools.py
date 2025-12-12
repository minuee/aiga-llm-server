import re
import os
from langchain_core.tools import tool, BaseTool
from langchain_core.prompts import ChatPromptTemplate # <--- Inject방지 
from .standard_desease_dic import STANDARD_DESEASE_DIC
from ..database.standardSpecialty import getStandardSpecialyByDB
from ..database.recommandDoctors import getRecommandDoctors
from ..database.doctor_paper import getDoctorPaper, getPatientMaxScore
from ..database.recommandHospital import getRecommandHospitals
from ..common.common import getValidHospitalName
from ..database.searchDoctor import getSearchDoctors, getSearchDoctorsByHospitalAndDept
from ..common.logger import logger

from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import create_sql_agent
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr

from ..config import settings

LIMIT_RECOMMAND_DOCTOR = int(os.environ['LIMIT_RECOMMAND_DOCTOR'])
LIMIT_RECOMMAND_PAPER = int(os.environ['LIMIT_RECOMMAND_PAPER'])
LIMIT_RECOMMAND_HOSPITAL = int(os.environ['LIMIT_RECOMMAND_HOSPITAL'])

# SQL agent를 위한 별도 설정
sql_llm = AzureChatOpenAI(
    azure_deployment=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=SecretStr(settings.azure_key),
    api_version=settings.azure_api_version,
    temperature=0
)

# SQLDatabase 인스턴스 생성
sql_db = SQLDatabase.from_uri(
    f"mysql+mysqlconnector://{settings.mysql_user}:{settings.mysql_password}@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}?collation=utf8mb4_general_ci"
)

# SQL agent 생성
sql_agent_executor = create_sql_agent(sql_llm, db=sql_db, agent_type="openai-tools", verbose=settings.sql_agent_verbose)


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
    standardSpecialty = getStandardSpecialyByDB(disease)
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

        # 학력 경력 3개까지만 표시
        # if doctor['jsondata']:
        #     matches = re.findall(r'{.*?}', doctor['jsondata'])
        #     if matches:
        #         matches = matches[:3]
        #     str_edu_careers = str(matches)
        # else:
        #     str_edu_careers = ""

        # 일단 논문 제거 아래 2줄
        # result = getDoctorPaper(doctor['rid'])
        # tritease = [data['title'] for data in result["data"]]

        # 값의 범위가 다름에 따라 최대값 구함. 
        # 최대값으로 나누어 0~5 사이의 값으로 변환(실제값 / max_value * 5)
        # 최종 각 점수를 0 ~ 1로 맞췄기 때문에 아래 max_value 값은 필요없음.
        # result = getPatientMaxScore()
        # max_kindness = result["data"][0]['max_kindness']
        # max_satisfaction = result["data"][0]['max_satisfaction']
        # max_explanation = result["data"][0]['max_explanation']
        # max_recommendation = result["data"][0]['max_recommendation']

        doctor = {
            "doctor_id": doctor.get('doctor_id'),
            "doctor_rid": doctor.get('hexrid', ''),
            "hospital": doctor.get('shortName', ''),
            "address": doctor.get('address', ''),
            "lat": doctor.get('lat'),
            "lon": doctor.get('lon'),
            "telephone": doctor.get('telephone', ''),
            "name": doctor.get('doctorname', ''),
            "deptname": doctor.get('deptname', ''),
            "specialties": doctor.get('specialties', ''),
            "url": doctor.get('doctor_url', ''),
            "education": doctor.get('education', ''),
            "career": doctor.get('career', ''),
            "paper": [], #tritease[:LIMIT_RECOMMAND_PAPER],
            "photo": doctor.get('profileimgurl', ''),
            "doctor_score": {
                "paper_score": doctor.get('paper_score', 0.0),
                "patient_score": doctor.get('patient_score', 0.0),
                "public_score": doctor.get('public_score', 0.0),
                # "peer_score": doctor.get('peer_score', 0.0)
                "peer_score": 0.0
            },
            "ai_score": {
                "kindness": doctor.get('kindness', 0.0) * 5.0,
                "satisfaction": doctor.get('satisfaction', 0.0) * 5.0,
                "explanation": doctor.get('explanation', 0.0) * 5.0,
                "recommendation": doctor.get('recommendation', 0.0) * 5.0
            },
            "review": []
        }
        doctorList.append(doctor)

    return doctorList

@tool
async def recommend_doctor(disease: str) -> dict:
    """질환명(disease) 기반 의사 추천 도구
    
    Args:
        disease: 필수 - 질환명
    """

    disease = disease.replace(" ", "").strip()
    # hospital = hospital.replace(" ", "").strip()
    
    ## - Noh logger.info(f"tool:recommend_doctor, disease:{disease}")
    
    if not disease:
        raise ValueError("질환명은 필수 입력값입니다.")
    
    # 표준진료/질환명 얻기
    standard_disease = await getStandardSpecialty(disease)
    ## - Noh logger.info(f"disease:{disease}, standard_disease:{standard_disease}")

    # 표준질환으로 추천 목록 얻기
    doctors = getRecommandDoctors(standard_disease, disease)
    # print(f"doctors: {doctors}")

    # 추천 의사 포맷팅
    formattedDoctors = formattingDoctorInfo(doctors["data"])

    # return f"{hospital} {disease} 전문의는 김상덕 교수입니다."

    result = {
        "chat_type": "recommand_doctor",
        "answer": {
            "disease": standard_disease if standard_disease else disease,
            "hospital": "",
            "doctors": [],
        }
    }

    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:LIMIT_RECOMMAND_DOCTOR]
    
    return result

@tool
async def recommend_hospital(department: str, count: int = 0) -> dict:
    """진료과(department) 기반 병원 추천 도구
    
    Args:
        department: 필수 - 진료과명
        count: 선택 - 추천 병원 수. 사용자가 요청한 병원 수를 지정합니다. 지정하지 않으면 기본값으로 추천합니다.
    """

    department = department.replace(" ", "").strip()
    logger.info(f"tool:recommend_hospital 시작 , department:{department}, count:{count}")

    if not department:
        raise ValueError("진료과명은 필수 입력값입니다.")
    

    limit = LIMIT_RECOMMAND_HOSPITAL
    if count > 0:
        limit = count

    hospitals = getRecommandHospitals(department,count)

    # return f"{department} 진료과는 고려대학교 병원을 추천합니다."

    result = {
        "chat_type": "recommand_hospital",
        "answer": {
            "department": department,
            "hospitals": [],
        }
    }

    if hospitals:
        result["answer"]["hospitals"] = hospitals[:limit]
    
    return result

@tool
def search_doctor(name: str, hospital:str = "", deptname:str = "") -> dict:
    """의사이름(name) 기반 의사 검색 도구
    
    Args:
        name: 필수 - 의사명
        hospital: 선택 - 병원명
        deptname: 선택 - 진료과명
    """

    name = name.replace(" ", "").strip()
    hospital = hospital.replace(" ", "").strip()
    deptname = deptname.replace(" ", "").strip()
    logger.info(f"tool:search_doctor 시작 : name:{name}, hospital:{hospital}, deptname:{deptname}")
    
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

    # 추천 의사 포맷팅
    formattedDoctors = formattingDoctorInfo(doctors)

    result = {
        "chat_type": "search_doctor",
        "answer": {
            "name": name,
            "hospital": hospital,
            "deptname": deptname,
            "doctors": [],
        }
    }

    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:LIMIT_RECOMMAND_DOCTOR]
    
    return result

@tool
def search_doctor_by_hospital(hospital: str, deptname: str) -> dict:
    """병원과 진료과 기반 의사 검색 도구
    
    Args:
        hospital: 필수 - 병원명
        deptname: 필수 - 진료과명
    """

    hospital = hospital.replace(" ", "").strip()
    deptname = deptname.replace(" ", "").strip()
    
    logger.info(f"tool:search_doctor_by_hospital 시작 : hospital:{hospital}, deptname:{deptname}")
    
    if not hospital:
        raise ValueError("병원명은 필수 입력값입니다.")
    
    if not deptname:
        raise ValueError("진료과명은 필수 입력값입니다.")
    
    hospital = getValidHospitalName(hospital)
    
    # 병원 + 진료과로 검색
    doctors = getSearchDoctorsByHospitalAndDept(hospital, deptname)

    # 추천 의사 포맷팅
    formattedDoctors = formattingDoctorInfo(doctors)

    result = {
        "chat_type": "search_doctor",
        "answer": {
            "name": "",
            "hospital": hospital,
            "deptname": deptname,
            "doctors": [],
        }
    }

    if formattedDoctors:
        result["answer"]["doctors"] = formattedDoctors[:LIMIT_RECOMMAND_DOCTOR]
    
    return result


# SQL Agent를 위한 프롬프트 템플릿 정의 (모듈 레벨에서 한 번만 생성)
SQL_AGENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
            너는 MySQL 데이터베이스 전문가야
            주어진 질문에 답하기 위해, 아래 규칙과 테이블 정보를 바탕으로 SQL 쿼리를 생성.
            특히 과거 대화 내용을 참고하여 쿼리를 생성하고 병원이름, 진료과목, 의사명을 판단하여 알맞게 쿼리의 조건절을 생성해야 한다.
            논문을 문의해 오면 "죄송합니다 논문의 경우는 당 서비스에서 제공되지 않고 있습니다" 라는 내용을 포함해서 전체적으로 답변을 해야 한다.
            의사명의 경우는 like를 활용하세요 예) doctorname like '홍길동%'

            [엄격한 규칙]
            1. 쿼리는 반드시 MySQL 8.x 문법으로 작성한다.
            2. SELECT 조회 쿼리만 생성합니다. INSERT, UPDATE, DELETE 등 다른 DML, DDL은 절대 사용하지 않는다.
            3. 항상 LIMIT 절을 포함하여 반환 데이터 수를 30개로 제한한다.
            4. 아래 [테이블 및 컬럼 정보]에 명시된 테이블과 컬럼만 사용해야 한다. 명시되지 않은 테이블이나 컬럼은 절대 쿼리에 포함시키지 말라.
            5. 만약 사용자의 질문에 해당하는 정보(예: 출신학교, 국적)를 [테이블 및 컬럼 정보]에서 찾을 수 없다면, 쿼리를 생성하지 말고, "제가 가진 정보에서는 요청하신 내용이 포함되어 있지 않아 답변 드리기 어렵습니다." 와 같이 구체적으로 답변해야 한다.
            6. 모든 컬럼은 명시적 테이블 alias를 사용한다.

            [JOIN 규칙]
            - doctor.rid = doctor_basic.rid // and 조건에 doctor_basic.is_active not in ( 0,'0' ) 고정
            - doctor_basid.rid = doctor_career.rid // and 조건에 doctor_basic.is_active not in ( 0,'0' ) 고정
            - hospital.hid = doctor_basic.hid
            - hospital.hid = hospital_evaluation.hid >> LEFT JOIN
            - doctor.doctor_id =  doctor_evaluation.doctor_id >> LEFT JOIN

            [사용안하는 테이블] - 조회 금지
            - chat_history, data_history
            - errorlog, pwalog
            - checkpoint_blobs, checkpoint_migrations, checkpoint_writes, checkpoints
            - doctor_basic_logs, doctor_paper
            - hospital_alias, hospital_all_list, hospital_bedoc_list
            - patient_review, patient_review_source
            - specialty, standard_dept_spec, standardspecialty

            [테이블 및 컬럼 정보]
            - hospital: 병원 정보
                - 컬럼: hid, baseName:(병원명), address:(병원주소),lat:(위도),lon:(경도),telephone:(전화번호)
            - hospital_evaluation : 병원의 평가정보
                - 컬럼: hid, matched_dept : (진료과목), public_score : ( 평가점수 )
            - doctor: 의사의 고유 ID 정보
                - 컬럼: doctor_id, rid, name:(의사명), is_active:(활성여부:0은 제외)
            - doctor_basic: 의사의 기본 프로필 and 조건에 is_active not in ( 0,'0' ) 고정
                - 컬럼: doctor_id, rid, doctorname:(의사명), hid:(병원ID), deptname:(진료과목), specialties:(전문분야), doctor_url:(의사홈페이지), profileimgurl:(의사사진)
            - doctor_career: 의사의 학력, 경력, 기타정보 json형태이나 string 문자열로 저장되어 있음
                - 컬럼: rid, education(학력),career(경력),etc(기타 학회,학술,수상,저서,언론,기타)
            - doctor_evaluation : 의사의 평가정보 필수정보가 아님
                - 컬럼: doctor_id, kindness:(친절도), satisfaction:(만족도), explanation:(설명), recommendation:(추천), paper_score:(논문점수), patient_score:(환자점수), public_score:(공정점수)

            [출력 형식]
            - 2개이상의 정보가 출력시 가장 유사한 정보 1개를 우선해서 출력한다.
            - 테이블의 컬럼의 hid,rid,doctor_id,hid는 제외한 나머지 정보를 부드럽게 표현해서 출력한다.
            - **테이블의 컬럼에서 긴 문자열 데이터(예: 학력, 경력, 기타정보)는 사용자의 질문과 관련된 핵심 내용을 200자 이내로 간결하게 요약하여 제공해야 한다.**
            """,
        ),
    ("human", "{question}"), # 사용자의 질문은 여기에 삽입됩니다.
    ]
)


@tool
async def search_doctor_for_else_question(question: str) -> dict:
    """그밖의 질문(question)에 대한 응답 도구 - SQL agent를 사용하여 테이블 정보를 기반으로 답변

    Args:
        question: 필수 - 질문내용
    """
    logger.info(f"tool: search_doctor_for_else_question 시작 - question: {question}")

    try:
        # 1. PromptTemplate을 사용하여 지시문과 사용자 질문을 결합합니다.
        # .format_messages()는 시스템/인간 메시지 객체의 리스트를 반환합니다.
        # SQL Agent의 'input'은 보통 문자열을 기대하므로, .format()으로 최종 문자열을 만듭니다.
        final_input = SQL_AGENT_PROMPT.format(question=question)

        # 2. SQL agent를 비동기(ainvoke)로 호출합니다.
        result = await sql_agent_executor.ainvoke({
             "input": final_input
        })
        
        logger.info(f"tool: SQL agent 결과 -n: {result}")
        answer = result.get("output", "답변을 찾을 수 없습니다.")
       
        return {
            "chat_type": "general",
            "answer": answer
        }
    
    except Exception as e:
        print(f"tool: search_doctor_for_else_question 에러 - {str(e)}")
        return {
            "chat_type": "general",
            "answer": f"데이터베이스 조회 중 오류가 발생했습니다: {str(e)}"
        }