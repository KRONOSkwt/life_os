"""Synchronization API with local development and HTTPS device modes."""

import json
import os
import secrets
from ipaddress import ip_address
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .auth import Credentials, PairingDenied
from .store import InvalidCursor, OperationReused, Store
from .postgres import open_store
from .validation import InvalidMessage, Messages


ROOT = Path(__file__).resolve().parents[3]
MAX_BODY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    database: Path
    token: str | None = None
    contract: Path = ROOT / "contracts" / "sync-v1.schema.json"
    web_directory: Path = ROOT / "apps" / "web" / "dist"
    mode: str = "development"
    database_url: str | None = None

    def __post_init__(self):
        if self.mode not in ("development", "paired"):
            raise ValueError("LIFE_OS_MODE must be development or paired")
        if self.mode == "development" and (
            not self.token or len(self.token) < 32 or not self.token.isascii()
            or not self.token.isprintable() or any(char.isspace() for char in self.token)
        ):
            raise ValueError("LIFE_OS_DEV_TOKEN must contain at least 32 ASCII characters")

    @classmethod
    def environment(cls):
        mode = os.environ.get("LIFE_OS_MODE", "development")
        return cls(
            database=Path(os.environ.get("LIFE_OS_DATABASE", ROOT / ".data" / "life-os.sqlite3")),
            token=os.environ.get("LIFE_OS_DEV_TOKEN") if mode == "development" else None,
            mode=mode,
            database_url=os.environ.get("LIFE_OS_DATABASE_URL"),
        )


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    pairing_code: str = Field(min_length=20, max_length=32)
    device_name: str = Field(min_length=1, max_length=80)


async def json_body(request: Request, maximum: int) -> object:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Expected application/json")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise HTTPException(413, "Request body is too large")
    try:
        return json.loads(chunks)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise HTTPException(422, "Invalid JSON body") from error


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.environment()
    messages = Messages(settings.contract)
    store = open_store(settings.database, settings.database_url)
    credentials = Credentials(store)
    app = FastAPI(title="Life OS API", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def transport(request: Request, call_next):
        if settings.mode == "paired" and request.url.scheme != "https":
            return JSONResponse({"detail": "HTTPS is required"}, status_code=400)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authenticated(request: Request, authorization: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        supplied = authorization.credentials if authorization else ""
        device = None
        if settings.mode == "development":
            try:
                local = request.client is not None and ip_address(request.client.host).is_loopback
            except ValueError:
                local = False
            if local and supplied.isascii() and secrets.compare_digest(supplied, settings.token):
                device = "local-development"
        else:
            device = credentials.authenticate(supplied)
        if device is None:
            raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
        return device

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "loopback-development" if settings.mode == "development" else "paired-https"}

    @app.post("/api/v1/pairing/exchange", openapi_extra={"requestBody": {
        "required": True, "content": {"application/json": {"schema": PairRequest.model_json_schema()}}
    }})
    async def pair(request: Request):
        if settings.mode != "paired":
            raise HTTPException(404, "Pairing is not enabled")
        if request.headers.get("origin"):
            raise HTTPException(403, "Pairing is available to native clients only")
        try:
            payload = PairRequest.model_validate(await json_body(request, 4096))
        except ValidationError as error:
            raise HTTPException(422, "Invalid pairing request") from error
        try:
            return await run_in_threadpool(credentials.exchange, payload.pairing_code, payload.device_name)
        except PairingDenied as error:
            raise HTTPException(401, str(error)) from error

    @app.post("/api/v1/sync/push")
    async def push(request: Request, device: str = Depends(authenticated)):
        body = await json_body(request, MAX_BODY_BYTES)
        try:
            payload = messages.push(body)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            detail = str(error) if isinstance(error, InvalidMessage) else "Invalid JSON body"
            raise HTTPException(422, detail) from error
        # Synchronous SQLite operations run in a worker, not on the event loop.
        def apply_batch():
            return [store.apply(device, operation) for operation in payload["operations"]]

        try:
            results = await run_in_threadpool(apply_batch)
        except OperationReused as error:
            raise HTTPException(409, str(error)) from error
        return {"protocol_version": 1, "results": results}

    @app.get("/api/v1/sync/pull")
    def pull(
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=100, ge=1, le=100),
        device: str = Depends(authenticated),
    ):
        try:
            return store.pull(cursor, limit)
        except InvalidCursor as error:
            raise HTTPException(400, str(error)) from error

    # API routes are registered first so the static mount cannot replace them.
    if (settings.web_directory / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.web_directory, html=True), name="web")

    return app
