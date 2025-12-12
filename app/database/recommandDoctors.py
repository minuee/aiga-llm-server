import os
from .db import fetchData
from ..common.logger import logger
from ..common.contant import EVAL_TYPE

def getRecommandDoctors(standard_disease:str, disease: str, evalType: EVAL_TYPE=EVAL_TYPE.TOTAL):
    """추천 의사 목록을 구하는 함수"""

    # 3차: 별도의 doctor 전문분야 테이블에서 검색해서 처리

    prefix_query = """SELECT s.shortName, s.address, s.lat, s.lon, s.telephone, b.doctorname, b.deptname, b.specialties, d.jsondata"""
         
    if not standard_disease:
        standard_disease = disease

        prefix_query += """,b.rid,HEX(b.rid) AS hexrid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career, 
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
        FROM 
            ( SELECT specialty_id,specialty FROM specialty WHERE specialty like :disease ) a
            LEFT JOIN doctor_specialty ds ON a.specialty_id = ds.specialty_id 
            LEFT JOIN doctor_evaluation e ON ds.doctor_id = e.doctor_id and a.specialty = e.standard_spec 
            LEFT JOIN doctor_basic b ON ds.doctor_id = b.doctor_id
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN hospital s ON b.hid = s.hid 
        WHERE 
            b.doctorname is not null AND b.doctor_id is not null
            AND b.is_active not in ( 0,'0' )
        ORDER BY total_score desc 
        LIMIT 15"""
    else:
        prefix_query += """,b.rid,HEX(b.rid) AS hexrid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career, 
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
        FROM 
            ( SELECT * FROM doctor_evaluation WHERE standard_spec like :disease ) e
            LEFT JOIN doctor_basic b ON e.doctor_id = b.doctor_id AND b.is_active not in ( 0,'0' )
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN hospital s ON b.hid = s.hid 
        WHERE 
            b.doctorname is not null and b.doctor_id is not null
        ORDER BY total_score desc 
        LIMIT 20"""

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