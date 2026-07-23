import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine, Base
from app.migrations import run_migrations
from app.routers import auth, campaigns, leads, businesses, upload, chat
from app.routers import ai

# Create database tables automatically
Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(
    title="REACHLO Backend API",
    description="Python FastAPI + MySQL Backend for REACHLO Mobile App",
    version="1.0.0"
)

from fastapi.staticfiles import StaticFiles
import os

# Create required directories if they don't exist
os.makedirs("uploads", exist_ok=True)
os.makedirs("uploads/ai-thumbnails", exist_ok=True)

# Configure CORS so mobile devices and web clients can access the APIs
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for uploads (including AI-generated thumbnails)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Register routers
app.include_router(auth.router, prefix="/api")
app.include_router(campaigns.router, prefix="/api")
app.include_router(leads.router, prefix="/api")
app.include_router(businesses.router, prefix="/api")
app.include_router(upload.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(chat.router, prefix="/api")

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "REACHLO API Service",
        "database": "MySQL Connected"
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
