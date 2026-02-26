import os
from .db import fetchData
from ..common.logger import logger
from ..common.contant import EVAL_TYPE

def getRecommandDoctors(standard_disease: list, disease: list, logical_operator: str = 'OR', evalType: EVAL_TYPE=EVAL_TYPE.TOTAL):
    """
    추천 의사 목록을 구하는 함수. 여러 질환에 대해 AND/OR 조건 검색을 지원.
    
    Args:
        standard_disease (list): 표준화된 질환명 리스트.
        disease (list): 사용자가 입력한 원본 질환명 리스트.
        logical_operator (str): 'AND' 또는 'OR'. 기본값은 'OR'.
        evalType (EVAL_TYPE): 평가 타입.
    """
    prefix_query = """SELECT s.shortname, s.address, s.lat, s.lon, s.telephone, s.hospital_site, s.hid, b.doctorname, b.deptname, b.specialties,
    b.rid,HEX(b.rid) AS hexrid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career,
    0 as paper_score,
    IFNULL(e.patient_score, 0) as patient_score,
    IFNULL(e.public_score, 0) as public_score,
    IFNULL(e.peer_score, 0) as peer_score,
    IFNULL(e.kindness, 0) as kindness,
    IFNULL(e.satisfaction, 0) as satisfaction,
    IFNULL(e.explanation, 0) as explanation,
    IFNULL(e.recommendation, 0) as recommendation
    """

    if evalType == EVAL_TYPE.TOTAL:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight + IFNULL(e.public_score, 0) * :score_weight) AS total_score"""
    elif evalType == EVAL_TYPE.PATIENT:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight) AS total_score"""

    param = {"score_weight": float(os.getenv("SCORE_WEIGHT", 0.3))}
    
    search_diseases = standard_disease if standard_disease else disease
    
    if not search_diseases:
        return {"data": []} # 검색할 질환이 없으면 빈 결과 반환

    if not standard_disease:
        # 표준 질환명이 없는 경우, 원본 질환명으로 LIKE 검색
        # 공백이 있는 질환명과 공백이 없는 질환명을 모두 검색하기 위한 로직
        param_idx = 0
        where_parts = []
        
        for d in search_diseases:
            # 원본 검색어 추가
            param_name = f"disease_{param_idx}"
            where_parts.append(f"specialty LIKE :{param_name}")
            param[param_name] = f"%{d}%"
            param_idx += 1

            # 질환명에 공백이 있는 경우, 공백을 제거한 버전도 OR 조건으로 추가
            if ' ' in d:
                d_no_space = d.replace(' ', '')
                param_name_no_space = f"disease_{param_idx}"
                where_parts.append(f"specialty LIKE :{param_name_no_space}")
                param[param_name_no_space] = f"%{d_no_space}%"
                param_idx += 1
        
        where_clauses = " OR ".join(where_parts)

        postfix_query = f"""
        FROM 
            ( SELECT specialty_id,specialty FROM specialty WHERE {where_clauses} ) a
            LEFT JOIN doctor_specialty ds ON a.specialty_id = ds.specialty_id 
            LEFT JOIN doctor_evaluation e ON ds.doctor_id = e.doctor_id and a.specialty = e.standard_spec 
            LEFT JOIN doctor_basic b ON ds.doctor_id = b.doctor_id
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN hospital s ON b.hid = s.hid 
        WHERE 
            b.doctorname is not null AND b.doctor_id is not null
            AND b.is_active in ('1','2')
        ORDER BY total_score desc 
        LIMIT 15"""
    else:
        # 표준 질환명이 있는 경우, 각 질환에 대한 플레이스홀더를 동적으로 생성
        in_clause_placeholders = ", ".join([f":disease_{i}" for i in range(len(search_diseases))])
        for i, d in enumerate(search_diseases):
            param[f"disease_{i}"] = d
        
        if logical_operator.upper() == 'AND' and len(search_diseases) > 1:
            # AND 조건: 모든 질환을 다루는 의사 검색
            
            subquery = f"""
                SELECT doctor_id
                FROM doctor_evaluation
                WHERE standard_spec IN ({in_clause_placeholders})
                GROUP BY doctor_id
                HAVING COUNT(DISTINCT standard_spec) = {len(search_diseases)}
            """
            
            # FROM 중복을 제거하고, GROUP BY 절을 명확히 수정
            postfix_query = f"""
            FROM
                ({subquery}) AS filtered_doctors
                JOIN doctor_basic b ON filtered_doctors.doctor_id = b.doctor_id
                LEFT JOIN doctor_evaluation e ON b.doctor_id = e.doctor_id AND e.standard_spec IN ({in_clause_placeholders})
                LEFT JOIN hospital s ON b.hid = s.hid
                LEFT JOIN doctor_career d ON b.rid = d.rid
            WHERE
                b.is_active IN ('1','2') AND b.doctorname IS NOT NULL
            GROUP BY 
                b.doctor_id, s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid,
                b.doctorname, b.deptname, b.specialties, b.rid, b.doctor_url,
                b.profileimgurl, d.education, d.career, e.patient_score, e.public_score,
                e.peer_score, e.kindness, e.satisfaction, e.explanation, e.recommendation
            ORDER BY
                total_score DESC
            LIMIT 20
            """
        else:
            # OR 조건 (또는 단일 질환): 하나 이상의 질환을 다루는 의사 검색
            postfix_query = f"""
            FROM
                doctor_evaluation e JOIN doctor_basic b ON e.doctor_id = b.doctor_id
                LEFT JOIN hospital s ON b.hid = s.hid
                LEFT JOIN doctor_career d ON b.rid = d.rid
            WHERE
                e.standard_spec IN ({in_clause_placeholders}) AND b.is_active IN ('1','2')  AND b.doctorname IS NOT NULL
            ORDER BY
                total_score DESC
            LIMIT 20
            """

    query = prefix_query + score_query + postfix_query
        
    logger.info(f"fetchData: Recommand Doctors with diseases: {search_diseases}, operator: {logical_operator} param : {param}")
    result = fetchData(query, param)
    
    return result

def getRecommandDoctorWithDiseaseAndDepartment(standard_disease: list, disease: list, department: str, logical_operator: str = 'OR', evalType: EVAL_TYPE=EVAL_TYPE.TOTAL, limit: int = 20):
    """
    추천 의사 목록을 구하는 함수. 질환과 진료과로 검색.
    
    Args:
        standard_disease (list): 표준화된 질환명 리스트.
        disease (list): 사용자가 입력한 원본 질환명 리스트.
        department (str): 진료과명.
        logical_operator (str): 'AND' 또는 'OR'. 기본값은 'OR'.
        evalType (EVAL_TYPE): 평가 타입.
        limit (int): 반환할 결과의 최대 수.
    """
    prefix_query = """SELECT s.shortname, s.address, s.lat, s.lon, s.telephone, s.hospital_site,s.hid, b.doctorname, b.deptname, b.specialties,
    b.rid,HEX(b.rid) AS hexrid, b.doctor_id, b.doctor_url, b.profileimgurl, d.education, d.career,
    0 as paper_score,
    IFNULL(e.patient_score, 0) as patient_score,
    IFNULL(e.public_score, 0) as public_score,
    IFNULL(e.peer_score, 0) as peer_score,
    IFNULL(e.kindness, 0) as kindness,
    IFNULL(e.satisfaction, 0) as satisfaction,
    IFNULL(e.explanation, 0) as explanation,
    IFNULL(e.recommendation, 0) as recommendation
    """

    if evalType == EVAL_TYPE.TOTAL:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight + IFNULL(e.public_score, 0) * :score_weight) AS total_score"""
    elif evalType == EVAL_TYPE.PATIENT:
        score_query = """,(IFNULL(e.patient_score, 0) * :score_weight) AS total_score"""

    param = {
        "score_weight": float(os.getenv("SCORE_WEIGHT", 0.3)),
        "department": f"%{department}%",
        "limit": limit
    }
    
    search_diseases = standard_disease if standard_disease else disease
    
    if not search_diseases:
        return {"data": []} # 검색할 질환이 없으면 빈 결과 반환

    if not standard_disease:
        # 표준 질환명이 없는 경우, 원본 질환명으로 LIKE 검색
        # 공백이 있는 질환명과 공백이 없는 질환명을 모두 검색하기 위한 로직
        param_idx = 0
        where_parts = []
        
        for d in search_diseases:
            # 원본 검색어 추가
            param_name = f"disease_{param_idx}"
            where_parts.append(f"specialty LIKE :{param_name}")
            param[param_name] = f"%{d}%"
            param_idx += 1

            # 질환명에 공백이 있는 경우, 공백을 제거한 버전도 OR 조건으로 추가
            if ' ' in d:
                d_no_space = d.replace(' ', '')
                param_name_no_space = f"disease_{param_idx}"
                where_parts.append(f"specialty LIKE :{param_name_no_space}")
                param[param_name_no_space] = f"%{d_no_space}%"
                param_idx += 1
        
        where_clauses = " OR ".join(where_parts)

        postfix_query = f"""
        FROM 
            ( SELECT specialty_id,specialty FROM specialty WHERE {where_clauses} ) a
            LEFT JOIN doctor_specialty ds ON a.specialty_id = ds.specialty_id 
            LEFT JOIN doctor_evaluation e ON ds.doctor_id = e.doctor_id and a.specialty = e.standard_spec 
            LEFT JOIN doctor_basic b ON ds.doctor_id = b.doctor_id
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN hospital s ON b.hid = s.hid 
        WHERE 
            b.doctorname is not null AND b.doctor_id is not null
            AND b.is_active in ('1','2')
            AND b.deptname LIKE :department
        ORDER BY total_score desc 
        LIMIT :limit"""
    else:
        # 표준 질환명이 있는 경우, 각 질환에 대한 플레이스홀더를 동적으로 생성
        in_clause_placeholders = ", ".join([f":disease_{i}" for i in range(len(search_diseases))])
        for i, d in enumerate(search_diseases):
            param[f"disease_{i}"] = d
        
        if logical_operator.upper() == 'AND' and len(search_diseases) > 1:
            # AND 조건: 모든 질환을 다루는 의사 검색
            
            subquery = f"""
                SELECT doctor_id
                FROM doctor_evaluation
                WHERE standard_spec IN ({in_clause_placeholders})
                GROUP BY doctor_id
                HAVING COUNT(DISTINCT standard_spec) = {len(search_diseases)}
            """
            
            # FROM 중복을 제거하고, GROUP BY 절을 명확히 수정
            postfix_query = f"""
            FROM
                ({subquery}) AS filtered_doctors
                JOIN doctor_basic b ON filtered_doctors.doctor_id = b.doctor_id
                LEFT JOIN doctor_evaluation e ON b.doctor_id = e.doctor_id AND e.standard_spec IN ({in_clause_placeholders})
                LEFT JOIN hospital s ON b.hid = s.hid
                LEFT JOIN doctor_career d ON b.rid = d.rid
            WHERE
                b.is_active IN ('1','2') AND b.doctorname IS NOT NULL
                AND b.deptname LIKE :department
            GROUP BY 
                b.doctor_id, s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid,
                b.doctorname, b.deptname, b.specialties, b.rid, b.doctor_url,
                b.profileimgurl, d.education, d.career, e.patient_score, e.public_score,
                e.peer_score, e.kindness, e.satisfaction, e.explanation, e.recommendation
            ORDER BY
                total_score DESC
            LIMIT :limit
            """
        else:
            # OR 조건 (또는 단일 질환): 하나 이상의 질환을 다루는 의사 검색
            postfix_query = f"""
            FROM
                doctor_evaluation e JOIN doctor_basic b ON e.doctor_id = b.doctor_id
                LEFT JOIN hospital s ON b.hid = s.hid
                LEFT JOIN doctor_career d ON b.rid = d.rid
            WHERE
                e.standard_spec IN ({in_clause_placeholders}) AND b.is_active IN ('1','2')  AND b.doctorname IS NOT NULL
                AND b.deptname LIKE :department
            ORDER BY
                total_score DESC
            LIMIT :limit
            """

    query = prefix_query + score_query + postfix_query
        
    logger.info(f"fetchData: Recommand Doctors with diseases: {search_diseases}, department: {department}, operator: {logical_operator} param : {param}")
    result = fetchData(query, param)
    
    return result