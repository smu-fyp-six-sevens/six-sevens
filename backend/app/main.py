from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .queue_service import document_queue
from .routers import cases, documents, monitoring, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    document_queue.start()
    yield


app = FastAPI(title="fyp API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cases.router)
app.include_router(upload.router)
app.include_router(documents.router)
app.include_router(monitoring.router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/message")
def message() -> dict[str, str]:
    return {"message": "Hello from FastAPI"}
