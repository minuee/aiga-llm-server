import os
from .db import fetchData
from ..common.logger import logger
from ..common.contant import EVAL_TYPE

def getRecommandDoctors(standard_disease:str, disease: str, evalType: EVAL_TYPE=EVAL_TYPE.TOTAL):
    """추천 의사 목록을 구하는 함수"""

    # 3차: 별도의 doctor 전문분야 테이블에서 검색해서 처리

    prefix_query = """select s.shortName, s.address, s.lat, s.lon, s.telephone, b.doctorname, b.deptname, b.specialties, d.jsondata"""
         
    if not standard_disease:
        standard_disease = disease

        prefix_query += """,b.rid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career, 
        IFNULL(e.paper_score, 0) as paper_score, 
        IFNULL(e.patient_score, 0) as patient_score, 
        IFNULL(e.public_score, 0) as public_score, 
        IFNULL(e.peer_score, 0) as peer_score,
        IFNULL(e.kindness, 0) as kindness, 
        IFNULL(e.satisfaction, 0) as satisfaction,
        IFNULL(e.explanation, 0) as explanation, 
        IFNULL(e.recommendation, 0) as recommendation
        """
          
        postfix_query = """
        from (select * from specialty where specialty like :disease) a
        left join doctor_specialty ds
        on a.specialty_id = ds.specialty_id 
        left join doctor_evaluation e
        on ds.doctor_id = e.doctor_id and a.specialty = e.standard_spec 
        left join doctor_basic b
        on ds.doctor_id = b.doctor_id
        left join doctor_career d
        on b.rid = d.rid
        left join hospital s 
        on b.hid = s.hid 
        where b.doctorname is not null and b.doctor_id is not null
        order by total_score desc limit 15"""
    else:
        prefix_query += """,b.rid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career, 
        IFNULL(e.paper_score, 0) as paper_score, 
        IFNULL(e.patient_score, 0) as patient_score, 
        IFNULL(e.public_score, 0) as public_score, 
        IFNULL(e.peer_score, 0) as peer_score,
        IFNULL(e.kindness, 0) as kindness, 
        IFNULL(e.satisfaction, 0) as satisfaction,
        IFNULL(e.explanation, 0) as explanation, 
        IFNULL(e.recommendation, 0) as recommendation
        """

        postfix_query = """
        from (select * from doctor_evaluation where standard_spec like :disease) e
        left join doctor_basic b
        on e.doctor_id = b.doctor_id
        left join doctor_career d
        on b.rid = d.rid
        left join hospital s 
        on b.hid = s.hid 
        where b.doctorname is not null and b.doctor_id is not null
        order by total_score desc limit 20"""

    if evalType == EVAL_TYPE.TOTAL:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight + IFNULL(e.paper_score, 0) * :score_weight + IFNULL(e.public_score, 0) * :score_weight) AS total_score"""
    elif evalType == EVAL_TYPE.PATIENT:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight) AS total_score"""
    elif evalType == EVAL_TYPE.PAPER:
        score_query = """,(IFNULL(e.paper_score, 0) * :score_weight) AS total_score"""

    query = prefix_query + score_query + postfix_query
        
    param = {"disease": f"%{standard_disease}%", "score_weight": os.getenv("SCORE_WEIGHT")}
    logger.debug(f"fechData: doctor_evaluation")
    result = fetchData(query, param)

    return result