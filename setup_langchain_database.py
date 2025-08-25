from app.config import settings
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

PYMYSQL_DATABASE_URL = (
    f"mysql+pymysql://{settings.mysql_user}"
    f":{settings.mysql_password}@{settings.mysql_host}"
    f":{settings.mysql_port}/{settings.mysql_db}"
)

def setup_database():
    """데이터베이스에 체크포인트 테이블을 생성합니다."""
    print("데이터베이스 설정 중...")
    try:
        with PyMySQLSaver.from_conn_string(PYMYSQL_DATABASE_URL) as checkpointer:
            checkpointer.setup()
        print("✅ 데이터베이스 설정 완료!")
    except Exception as e:
        print(f"❌ 데이터베이스 설정 실패: {e}")

if __name__ == "__main__":
    setup_database()