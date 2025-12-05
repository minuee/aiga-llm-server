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
    logger.info(f"tool:recommend_hospital, department:{department}, count:{count}")

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
    
    ## - Noh logger.info(f"tool:search_doctor, name:{name}, hospital:{hospital}, deptname:{deptname}")
    
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
    
    ## - Noh logger.info(f"tool:search_doctor_by_hospital, hospital:{hospital}, deptname:{deptname}")
    
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


@tool
async def search_doctor_for_else_question(question: str) -> dict:
    """그밖의 질문(question)에 대한 응답 도구 - SQL agent를 사용하여 테이블 정보를 기반으로 답변

    Args:
        question: 필수 - 질문내용
    """
    logger.info(f"tool: search_doctor_for_else_question 시작 - question: {question}")

# SQL Agent를 위한 프롬프트 템플릿 정의
    sql_agent_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
                당신은 병원 및 의사 데이터베이스 전문가입니다. 다음 테이블 정보를 참고하여 사용자의 질문에 답변해주세요.
                
                - 테이블 역할:
                    - hospital: 병원 정보
                    - doctor: 의사 정보(doctor_id, doctorname)
                    - doctor_basic: 의사의 기본 정보
                    - doctor_career: 의사의 학력(education), 경력(career)
                    - doctor_paper: 의사의 논문 정보, pmid가 반드시 null이 아닌 값들 대상
                    - patient_review: 의사에 대한 환자의 리뷰 정보
                    
                - 답변 규칙:
                    - 답변은 반드시 한글로 작성해야 합니다.
                    - 가능한 구체적인 정보를 포함하여 친절하게 설명해주세요.
                """,
            ),
        ("human", "{question}"), # 사용자의 질문은 여기에 삽입됩니다.
        ]
    )

    try:
        # 1. PromptTemplate을 사용하여 지시문과 사용자 질문을 결합합니다.
        # .format_messages()는 시스템/인간 메시지 객체의 리스트를 반환합니다.
        # SQL Agent의 'input'은 보통 문자열을 기대하므로, .format()으로 최종 문자열을 만듭니다.
        final_input = sql_agent_prompt.format(question=question)

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
            "chat_type": "search_doctor",
            "answer": {
                "question": question,
                "response": f"데이터베이스 조회 중 오류가 발생했습니다: {str(e)}",
                "source": "error"
            }
        }