from .db import fetchData

def getDoctorPaper(rid:str):
    """추천 의사의 논문 검색 함수"""
    
    query = """SELECT * FROM doctor_paper where rid = :rid and pmid is not null order by isFirstAuthor DESC, createAt limit 10"""
    param = {"rid": rid}
    result = fetchData(query, param)
    return result


def getPatientMaxScore():
    """환자리규 평점의 'kindness', 'satisfaction','explanation', 'recommendation' 값들의 최대값"""
    
    query_old = """
        SELECT 
            MAX(kindness) as max_kindness, 
            MAX(satisfaction) as max_satisfaction,
            MAX(explanation) as max_explanation,
            MAX(recommendation) as max_recommendation
        FROM 
            doctor_evaluation"""
    query = """
        SELECT
            (SELECT MAX(kindness) FROM doctor_evaluation) AS max_kindness,
            (SELECT MAX(satisfaction) FROM doctor_evaluation) AS max_satisfaction,
            (SELECT MAX(explanation) FROM doctor_evaluation) AS max_explanation,
            (SELECT MAX(recommendation) FROM doctor_evaluation) AS max_recommendation"""
    param = {}
    result = fetchData(query, param)
    return result
