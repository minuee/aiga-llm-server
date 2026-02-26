from ..config import settings
from ..common.utils import _get_final_limit
from .db import fetchData
from ..common.logger import logger

def getSearchDoctors(name: str, hospital: str = "", deptname: str = "") -> list:
    """
    의사 이름, 병원, 진료과를 기반으로 의사 정보를 검색하는 통합 함수 (개선안).
    N+1 쿼리 문제와 비효율적인 서브쿼리 구조를 해결.
    """
    ### logger.info(f"tool:getSearchDoctors 시작 , name:{name}, hospital:{hospital}, deptname:{deptname}")
    base_query = """
        SELECT
            s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid,  b.doctor_id, b.doctorname, b.deptname,
            b.specialties, b.doctor_url, b.profileimgurl,
            d.education, d.career,
            b.rid, HEX(b.rid) as hexrid,
            0 as paper_score,
            IFNULL(e.patient_score, 0) as patient_score,
            IFNULL(e.public_score, 0) as public_score,
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness,
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation,
            IFNULL(e.recommendation, 0) as recommendation
        FROM
            doctor_basic b JOIN hospital s ON b.hid = s.hid
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN
                (SELECT
                    doctor_id,
                    0 as paper_score, avg(patient_score) as patient_score,
                    avg(public_score) as public_score, avg(peer_score) as peer_score,
                    avg(kindness) as kindness, avg(satisfaction) as satisfaction,
                    avg(explanation) as explanation, avg(recommendation) as recommendation
                FROM
                    doctor_evaluation
                GROUP BY doctor_id
                ) e ON b.doctor_id = e.doctor_id
        WHERE
            b.doctorname LIKE :name
            AND b.is_active in ('1','2')
    """
    param = {"name": f"%{name}%"}

    # 병원 조건 동적 추가
    if hospital:
        base_query += " AND s.shortName = :hospital"
        param["hospital"] = hospital

    # 진료과 조건 동적 추가
    if deptname:
        base_query += " AND b.deptname LIKE :deptname"
        param["deptname"] = f"%{deptname}%"

    logger.debug(f"Executing getSearchDoctors query with params: {param}")
    result = fetchData(base_query, param)
    return result.get("data", [])

from typing import Union, List, Optional

def getSearchDoctorsByHospitalAndDept(hospital: str, deptname: Union[str, List[str]]) -> list:
    """병원과 하나 또는 여러 진료과로 의사를 검색하는 함수"""

    # 쿼리 템플릿의 WHERE 절을 format을 사용해 동적으로 구성할 수 있도록 변경
    base_query = """
        SELECT
            s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid,  b.doctor_id, b.doctorname, b.deptname,
            b.specialties, b.doctor_url, b.profileimgurl,
            d.education, d.career,
            b.rid, HEX(b.rid) as hexrid,
            0 as paper_score,
            IFNULL(e.patient_score, 0) as patient_score,
            IFNULL(e.public_score, 0) as public_score,
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness,
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation,
            IFNULL(e.recommendation, 0) as recommendation
        FROM
            doctor_basic b
        JOIN
            hospital s ON b.hid = s.hid
        LEFT JOIN
            doctor_career d ON b.rid = d.rid
        LEFT JOIN
            (SELECT
                doctor_id,
                0 as paper_score, avg(patient_score) as patient_score,
                avg(public_score) as public_score, avg(peer_score) as peer_score,
                avg(kindness) as kindness, avg(satisfaction) as satisfaction,
                avg(explanation) as explanation, avg(recommendation) as recommendation
             FROM
                doctor_evaluation
             GROUP BY doctor_id
            ) e ON b.doctor_id = e.doctor_id
        WHERE
            s.shortName = :hospital
            {dept_condition}
            AND b.is_active in ('1','2')
        ORDER BY
            e.patient_score DESC
    """
    param = {"hospital": hospital}

    dept_list = deptname if isinstance(deptname, list) else [deptname]
    
    # 여러 진료과에 대한 'b.deptname LIKE :key' 구문을 'OR'로 연결
    dept_clauses = []
    for i, d_name in enumerate(dept_list):
        key = f"dept_{i}"
        dept_clauses.append(f"b.deptname LIKE :{key}")
        param[key] = f"%{d_name}%"
    
    dept_where_clause = " OR ".join(dept_clauses)
    dept_condition = f"AND ({dept_where_clause})" if dept_where_clause else ""

    query = base_query.format(dept_condition=dept_condition)

    logger.debug(f"Executing getSearchDoctorsByHospitalAndDept query: {query} with params: {param}")
    result = fetchData(query, param)
    return result.get("data", [])

def getDoctorById(doctor_id: int) -> list:
    """의사 ID로 상세 정보를 가져오는 함수 (개선안)"""

    query = """
        SELECT
            b.doctorname, b.deptname, s.shortName as hospital_name
        FROM
            doctor_basic b
        JOIN
            hospital s on b.hid = s.hid
        WHERE
            b.doctor_id = :doctor_id
            AND b.is_active in ('1','2')
        LIMIT 1
    """
    param = {"doctor_id": doctor_id}
    
    logger.debug(f"Fetching doctor info for getDoctorById with doctor_id: {doctor_id}")
    result = fetchData(query, param)
    
    if not result.get("data"):
        return []

    doctor_info = result["data"][0]
    name = doctor_info['doctorname']
    hospital = doctor_info['hospital_name']
    deptname = doctor_info['deptname']

    # 통합된 getSearchDoctors 함수를 호출하여 전체 정보 조회
    return getSearchDoctors(name, hospital, deptname)

def getSearchDoctorsByOnlyHospital(hospital: str) -> list:
    """병원으로 의사를 검색하는 함수 (개선안)"""

    query = """
        SELECT
            s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid, b.doctor_id, b.doctorname, b.deptname,
            b.specialties, b.doctor_url, b.profileimgurl,
            d.education, d.career,
            b.rid, HEX(b.rid) as hexrid,
            0 as paper_score,
            IFNULL(e.patient_score, 0) as patient_score,
            IFNULL(e.public_score, 0) as public_score,
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness,
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation,
            IFNULL(e.recommendation, 0) as recommendation
        FROM
            doctor_basic b
        JOIN
            hospital s ON b.hid = s.hid
        LEFT JOIN
            doctor_career d ON b.rid = d.rid
        LEFT JOIN
            (SELECT
                doctor_id,
                0 as paper_score, avg(patient_score) as patient_score,
                avg(public_score) as public_score, avg(peer_score) as peer_score,
                avg(kindness) as kindness, avg(satisfaction) as satisfaction,
                avg(explanation) as explanation, avg(recommendation) as recommendation
             FROM
                doctor_evaluation
             GROUP BY doctor_id
            ) e ON b.doctor_id = e.doctor_id
        WHERE
            s.shortName = :hospital
            AND b.is_active in ('1','2')
        ORDER BY
            e.patient_score DESC
    """
    param = {"hospital": hospital}

    logger.debug(f"Executing getSearchDoctorsByOnlyHospital query with params: {param}")
    result = fetchData(query, param)
    return result.get("data", [])
    


def getSearchDoctorsByOnlyDepartment(department: Union[str, List[str]], limit: Optional[int] = None) -> list:
    """진료과목으로만 의사를 검색하는 함수 (개선안)"""

    base_query = """
        SELECT
            s.shortname, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid, b.doctor_id, b.doctorname, b.deptname,
            b.specialties, b.doctor_url, b.profileimgurl,
            d.education, d.career,
            b.rid, HEX(b.rid) as hexrid,
            0 as paper_score,
            IFNULL(e.patient_score, 0) as patient_score,
            IFNULL(e.public_score, 0) as public_score,
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness,
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation,
            IFNULL(e.recommendation, 0) as recommendation
        FROM
            doctor_basic b
            JOIN hospital s ON b.hid = s.hid
            LEFT JOIN doctor_career d ON b.rid = d.rid
            LEFT JOIN
            (
                SELECT
                    doctor_id,
                    0 as paper_score, avg(patient_score) as patient_score,
                    avg(public_score) as public_score, avg(peer_score) as peer_score,
                    avg(kindness) as kindness, avg(satisfaction) as satisfaction,
                    avg(explanation) as explanation, avg(recommendation) as recommendation
                FROM
                    doctor_evaluation
                GROUP BY doctor_id
            ) e ON b.doctor_id = e.doctor_id
        WHERE
            b.is_active in ('1','2')
            {dept_condition}
        ORDER BY
            e.patient_score DESC
        LIMIT {limit}
    """
    param = {}

    dept_list = department if isinstance(department, list) else [department]
    
    dept_clauses = []
    for i, d_name in enumerate(dept_list):
        key = f"dept_{i}"
        dept_clauses.append(f"b.deptname LIKE :{key}")
        param[key] = f"%{d_name}%"
    
    dept_where_clause = " OR ".join(dept_clauses)
    dept_condition = f"AND ({dept_where_clause})" if dept_where_clause else ""

    final_limit = _get_final_limit(limit)

    query = base_query.format(dept_condition=dept_condition, limit=final_limit)
    
    logger.debug(f"Executing getSearchDoctorsByOnlyDepartment query with params: {param}")
    result = fetchData(query, param)
    return result.get("data", [])