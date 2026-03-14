from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import endpoints, oauth
from app.database import engine, ensure_sqlite_schema_compat
from app import models

models.Base.metadata.create_all(bind=engine)
ensure_sqlite_schema_compat(engine)

app = FastAPI(title="Antigravity API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Antigravity API is running"}

app.include_router(endpoints.router, prefix="/api")
app.include_router(oauth.router, prefix="/api/auth", tags=["auth"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
