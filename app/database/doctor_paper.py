from .db import fetchData

def getDoctorPaper(rid:str):
    """추천 의사의 논문 검색 함수"""
    
    query = """select * from doctor_paper where rid = :rid"""
    param = {"rid": rid}
    result = fetchData(query, param)
    return result


def getPatientMaxScore():
    """환자리규 평점의 'kindness', 'satisfaction','explanation', 'recommendation' 값들의 최대값"""
    
    query = """select 
    max(kindness) as max_kindness, 
    max(satisfaction) as max_satisfaction,
    max(explanation) as max_explanation,
    max(recommendation) as max_recommendation
    from aiga2025.doctor_evaluation"""
    param = {}
    result = fetchData(query, param)
    return result
