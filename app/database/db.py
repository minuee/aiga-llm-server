from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from ..config import settings
from ..common.logger import logger

DATABASE_URL = (
    f"mysql+mysqlconnector://{settings.mysql_user}"
    f":{settings.mysql_password}@{settings.mysql_host}"  
    f":{settings.mysql_port}/{settings.mysql_db}?collation=utf8mb4_general_ci"
)

logger.info(f'DATABASE_URL: {DATABASE_URL}')

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

logger.info(f'engine: {engine}')

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def fetchData(query, param): 
    sql = text(query)
    logger.debug(f"param: {param}\nquery: {sql}")

    try:
        with engine.connect() as connection:
            result = connection.execute(sql, param)
            fetch_data = result.fetchall()
            keys = result.keys()
            # Row 객체를 dict로 변환
            data = [dict(zip(keys, row)) for row in fetch_data]
            return {
                "column": list(keys),
                "data": data
            }
    except Exception as e:
        logger.error(f"DB Error: {e}", exc_info=True)
        return {
            "column": [],
            "data": []
        }