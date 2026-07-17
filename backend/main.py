from fastapi import FastAPI

from backend.routers import agent, projects, roi, upload

app = FastAPI(title="Vici ROI API")

app.include_router(projects.router)
app.include_router(upload.router)
app.include_router(roi.router)
app.include_router(agent.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
