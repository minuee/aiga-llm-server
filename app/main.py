from fastapi import FastAPI
from .routers.chat import router as chat_router
from .common.logger import setup_logger
from .agent import get_compiled_graph

# Initialize logger
logger = setup_logger()


app = FastAPI(title="FastAPI LangChain API")
app.include_router(chat_router)

# Add startup event handler
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup event triggered.")
    app.state.graph = await get_compiled_graph()
    logger.info("LangGraph compiled successfully and stored in app.state.graph.")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# def main():
#     print("Hello from aiga-llm-server!")

# if __name__ == "__main__":
#     main()
