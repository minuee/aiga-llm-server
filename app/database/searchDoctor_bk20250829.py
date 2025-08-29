from .db import fetchData
from ..common.logger import logger

def buildDoctorQuery(name: str, hospital: str, deptname: str) -> tuple:
    """의사 정보를 가져오기 위한 쿼리와 파라미터를 생성하는 함수"""
    
    if hospital:
        query = "SELECT * FROM hospital where baseName = :hospital or shortName = :hospital"
        param = {"hospital": hospital}

        logger.debug(f"fechData: hospital")
        result = fetchData(query, param)
        hid = result["data"][0]['hid']
        
        logger.debug(f"result, hid:{hid}")

        if deptname:
            query = """
            SELECT 
                s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
                a.specialties, a.doctor_url, a.profileimgurl,
                b.education, b.career, b.jsondata, a.rid,a.hexrid,
                IFNULL(e.paper_score, 0) as paper_score, 
                IFNULL(e.patient_score, 0) as patient_score, 
                IFNULL(e.public_score, 0) as public_score, 
                IFNULL(e.peer_score, 0) as peer_score,
                IFNULL(e.kindness, 0) as kindness, 
                IFNULL(e.satisfaction, 0) as satisfaction,
                IFNULL(e.explanation, 0) as explanation, 
                IFNULL(e.recommendation, 0) as recommendation
            FROM 
                ( 
                    SELECT
                        HEX(rid) AS hexrid,rid,doctor_id,hid,doctorname,deptname,specialties,doctor_url,profileimgurl
                    FROM 
                        doctor_basic
                    WHERE 
                        doctor_id is not null and hid = :hid and deptname = :deptname and doctorname = :name 
                ) a
                LEFT JOIN doctor_career b ON a.rid = b.rid 
                LEFT JOIN (
                    select 
                        doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
                        avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                    FROM 
                        doctor_evaluation 
                    GROUP BY doctor_id
                ) e ON a.doctor_id = e.doctor_id
                LEFT JOIN hospital s ON a.hid = s.hid"""
            param = {"hid": hid, "deptname": deptname, "name": name}
        else:
            query = """
            SELECT 
                s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
                a.specialties, a.doctor_url, a.profileimgurl,
                b.education, b.career, b.jsondata, a.rid,a.hexrid,
                IFNULL(e.paper_score, 0) as paper_score, 
                IFNULL(e.patient_score, 0) as patient_score, 
                IFNULL(e.public_score, 0) as public_score, 
                IFNULL(e.peer_score, 0) as peer_score,
                IFNULL(e.kindness, 0) as kindness, 
                IFNULL(e.satisfaction, 0) as satisfaction,
                IFNULL(e.explanation, 0) as explanation, 
                IFNULL(e.recommendation, 0) as recommendation
            FROM 
                ( 
                    SELECT
                        HEX(rid) AS hexrid,rid,doctor_id,hid,doctorname,deptname,specialties,doctor_url,profileimgurl 
                    FROM 
                        doctor_basic 
                    WHERE 
                        doctor_id is not null and hid = :hid and doctorname = :name 
                ) a
                LEFT JOIN doctor_career b ON a.rid = b.rid 
                LEFT JOIN (
                    SELECT 
                        doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                    FROM 
                        doctor_evaluation 
                    GROUP BY doctor_id
                ) e on a.doctor_id = e.doctor_id
                LEFT JOIN hospital s ON a.hid = s.hid"""
            param = {"hid": hid, "name": name}
       
    else:
        query = """
        SELECT 
            s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname,
            a.specialties, a.doctor_url, a.profileimgurl,
            b.education, b.career, b.jsondata, a.rid,a.hexrid,
            IFNULL(e.paper_score, 0) as paper_score, 
            IFNULL(e.patient_score, 0) as patient_score, 
            IFNULL(e.public_score, 0) as public_score, 
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness, 
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation, 
            IFNULL(e.recommendation, 0) as recommendation
        FROM 
            ( 
                SELECT
                    HEX(rid) AS hexrid,rid,doctor_id,hid,doctorname,deptname,specialties,doctor_url,profileimgurl
                FROM 
                    doctor_basic
                WHERE
                    doctor_id is not null and doctorname = :name 
            ) a
            LEFT JOIN doctor_career b ON a.rid = b.rid 
            LEFT JOIN (
                SELECT 
                    doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                FROM 
                    doctor_evaluation 
                GROUP BY doctor_id
            ) e ON a.doctor_id = e.doctor_id
            LEFT JOIN hospital s ON a.hid = s.hid"""     
        param = {"name": name}

    logger.debug(f"fechData: doctor_career")
    return query, param

def getSearchDoctors(name: str, hospital: str = "", deptname: str = "") -> dict:
    """의사 상세 정보를 가져오는 함수"""

    query, param = buildDoctorQuery(name, hospital, deptname)

    result = fetchData(query, param)["data"]
    return result

def getSearchDoctorsByHospitalAndDept(hospital: str, deptname: str) -> dict:
    """병원과 진료과로 의사를 검색하는 함수"""
    
    # 병원 ID 가져오기
    query = "SELECT * FROM hospital WHERE baseName = :hospital or shortName = :hospital"
    param = {"hospital": hospital}
    
    logger.debug(f"fechData: hospital")
    result = fetchData(query, param)
    
    if not result["data"]:
        return []
    
    hid = result["data"][0]['hid']
    
    # 해당 병원의 해당 진료과 의사들 가져오기
    query = """
        SELECT 
            s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
            a.specialties, a.doctor_url, a.profileimgurl,
            b.education, b.career, b.jsondata, a.rid,
            IFNULL(e.paper_score, 0) as paper_score, 
            IFNULL(e.patient_score, 0) as patient_score, 
            IFNULL(e.public_score, 0) as public_score, 
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness, 
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation, 
            IFNULL(e.recommendation, 0) as recommendation
        FROM 
            ( SELECT * FROM doctor_basic WHERE doctor_id is not null and hid = :hid and deptname = :deptname) a
            LEFT JOIN doctor_career b ON a.rid = b.rid 
            LEFT JOIN (
                SELECT 
                    doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
                    avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                FROM 
                    doctor_evaluation 
                GROUP BY doctor_id
            ) e ON a.doctor_id = e.doctor_id
            LEFT JOIN hospital s ON a.hid = s.hid
        ORDER BY e.paper_score desc, e.patient_score desc"""
    
    param = {"hid": hid, "deptname": deptname}
    
    logger.debug(f"fechData: doctors by hospital and dept")
    result = fetchData(query, param)["data"]
    return result

def getDoctorById(doctor_id: int) -> dict:
    """의사 상세 정보를 가져오는 함수"""

    query = """
        SELECT 
            b.doctorname, b.deptname, c.shortname 
        FROM doctor a
            LEFT JOIN doctor_basic b on a.doctor_id = b.doctor_id
            LEFT JOIN hospital c on b.hid = c.hid
        WHERE 
            a.doctor_id = :doctor_id"""

    param = {"doctor_id": doctor_id}
    result = fetchData(query, param)["data"]

    name = result[0]['doctorname']
    hospital = result[0]['shortname']
    deptname = result[0]['deptname']

    query, param = buildDoctorQuery(name, hospital, deptname)

    result = fetchData(query, param)["data"]
    return result    