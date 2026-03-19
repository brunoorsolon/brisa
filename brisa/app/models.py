from pydantic import BaseModel


class Settings(BaseModel):
    interval_seconds: int = 60
    history_days: int = 30
    safety_floor_percent: int = 30


class CurvePoint(BaseModel):
    temp: float
    percent: int


class Curve(BaseModel):
    name: str
    points: list[CurvePoint]


class FanConfig(BaseModel):
    fan_id: str
    fan_label: str
    curve_name: str
    sensor_id: str
    override_percent: int | None = None


class AppConfig(BaseModel):
    settings: Settings = Settings()
    curves: list[Curve] = []
    fan_configs: list[FanConfig] = []
    sensor_aliases: dict[str, str] = {}

