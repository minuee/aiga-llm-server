from .db import fetchData
from app.common.common import calculate_similarity
from ..common.logger import logger
import os

def getStandardSpecialyByDB(disease: str):
    """표준 진료 분야를 구하는 함수"""
    
    # 2차: 문자열 매칭해서 복수의 표준진료분야 제시해 유저가 선택
    logger.debug("2차-1: doctor_evaluation의 표준진료분야 매칭")
    query = """SELECT standard_spec FROM doctor_evaluation GROUP BY standard_spec having standard_spec = :disease"""
    param = {"disease": disease}
    logger.debug(f"fechData: doctor_evaluation")
    result = fetchData(query, param)["data"]
    if len(result) == 1:
        return result[0]['standard_spec']
    
    logger.debug("2차-2: doctor_evaluation의 표준진료분야와 입력 질환과의 음절 매칭")
    query = """SELECT standard_spec FROM doctor_evaluation GROUP BY standard_spec ORDER BY standard_spec"""
    logger.debug(f"fechData: standard_spec")
    result = fetchData(query, {})["data"]
    condidate_disease = {}
    max_val = -1
    similarity_disease = None
    for standard_spec_row in result:
        standard_spec = standard_spec_row['standard_spec']
        if standard_spec:
            val = calculate_similarity(disease, standard_spec)
            disease_synonym_match_rate = float(os.getenv("DISEASE_SYNONYM_MATCH"))/100
            if val >= disease_synonym_match_rate and val > max_val:
                similarity_disease = standard_spec
                max_val = val

    logger.debug(f"similarity_disease: {similarity_disease}")
    return similarity_disease
    
