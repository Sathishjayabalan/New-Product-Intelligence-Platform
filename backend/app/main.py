from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router
from .config import get_settings
from .database import init_db

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Ensure the default demo tenant exists.
    from .database import SessionLocal
    from .models import Tenant

    db = SessionLocal()
    try:
        slug = get_settings().default_tenant_slug
        if not db.query(Tenant).filter(Tenant.slug == slug).first():
            db.add(Tenant(slug=slug, name="Demo Tenant", industry_context="insurance"))
            db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="CapabilityOS",
    description=(
        "AI-Powered Product Intelligence Platform — NS19 four-engine pipeline "
        "(Listening → Behavioral → Prototype → Building)."
    ),
    version=__version__,
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "CapabilityOS", "version": __version__}


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "static" / "index.html")
