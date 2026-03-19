import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import load_config
from app.database import init_db
from app.models import AppConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_config: AppConfig | None = None
_loop_task: asyncio.Task | None = None


def get_config() -> AppConfig:
    if _config is None:
        raise RuntimeError("Config not loaded")
    return _config


def set_config(config: AppConfig) -> None:
    global _config
    _config = config


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop_task

    logger.info("Brisa starting up")

    config = load_config()
    set_config(config)
    logger.info("Config loaded: %d curve(s), %d fan config(s)",
                len(config.curves), len(config.fan_configs))

    init_db()

    from app.controller import loop
    _loop_task = asyncio.create_task(loop())

    yield

    logger.info("Brisa shutting down")
    if _loop_task:
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Brisa",
    description="Docker-based fan control service",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")


@app.get("/metrics", include_in_schema=False)
async def metrics():
    from app.hwmon import detect_sensors
    from app.liquidctl_wrapper import get_fan_status

    sensors = detect_sensors()
    fans = get_fan_status()

    lines = []

    lines.append("# HELP brisa_temperature_celsius Current temperature reading")
    lines.append("# TYPE brisa_temperature_celsius gauge")
    for s in sensors:
        label = s["label"].replace('"', '\\"')
        driver = s["driver"].replace('"', '\\"')
        lines.append(
            f'brisa_temperature_celsius{{sensor="{driver}",label="{label}"}} {s["current_temp"]}'
        )

    lines.append("# HELP brisa_fan_rpm Current fan RPM")
    lines.append("# TYPE brisa_fan_rpm gauge")
    for f in fans:
        fan_id = f["id"].replace('"', '\\"')
        fan_label = f["label"].replace('"', '\\"')
        lines.append(
            f'brisa_fan_rpm{{fan="{fan_id}",label="{fan_label}"}} {f["current_rpm"]}'
        )

    return PlainTextResponse("\n".join(lines) + "\n")


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
