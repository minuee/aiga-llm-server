from fastapi import FastAPI
from .routers.chat import router as chat_router

app = FastAPI(title="FastAPI LangChain API")
app.include_router(chat_router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# def main():
#     print("Hello from aiga-llm-server!")

# if __name__ == "__main__":
#     main()
