import os
import pathlib
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from .config import get_settings
from .db import engine
from .routes import admin, auth, public

app = FastAPI(title="LATCHA", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p.startswith("/api/admin"):
        resp.headers["Cache-Control"] = "no-store"          # never cache owner data
    elif p == "/api/menu" and request.method == "GET":
        # shared cache: fast menu even while Neon is waking up; edits show within ~1 minute
        resp.headers["Cache-Control"] = "public, s-maxage=60, stale-while-revalidate=300"
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


app.include_router(public)
app.include_router(auth)
app.include_router(admin)


@app.get("/api/health")
def health():
    with engine.connect() as c:  # also proves the database answers
        c.execute(text("select 1"))
    return {"ok": True}


# The website. API routes above win; anything else is looked up in site/.
# Unknown paths reach the app and return a normal 404.
SITE = pathlib.Path(__file__).resolve().parents[1] / "site"
if SITE.is_dir():
    # relative "site" (from the project root) is the form Vercel's docs use; absolute is the fallback
    app.frontend("/", directory="site" if os.path.isdir("site") else SITE, fallback=None)
