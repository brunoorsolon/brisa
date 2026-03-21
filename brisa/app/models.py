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
    backend: str = "liquidctl"  # "liquidctl" or "hwmon-pwm"


class VirtualSensor(BaseModel):
    id: str
    name: str
    source_sensor_ids: list[str]
    aggregation: str  # "avg", "min", "max"


class DashboardGroup(BaseModel):
    id: str
    name: str
    type: str  # "sensor" or "fan"
    item_ids: list[str] = []


class AppConfig(BaseModel):
    settings: Settings = Settings()
    curves: list[Curve] = []
    fan_configs: list[FanConfig] = []
    sensor_aliases: dict[str, str] = {}
    virtual_sensors: list[VirtualSensor] = []
    dashboard_groups: list[DashboardGroup] = []
    card_colors: dict[str, str] = {}  # sensor/fan ID → color key