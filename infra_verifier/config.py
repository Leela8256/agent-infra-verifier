"""Small, strict, versioned run contract. No silent misspelled budget fields."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Config:
    schema_version: int = 1
    profile: str = "local-pilot-v1-not-frozen"
    wall_clock_s: float = 300
    cpu_time_s: float | None = None
    memory_mb: float | None = None
    model_calls: int | None = None
    tool_calls: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    steps: int | None = None
    sample_interval_s: float = 0.05
    require_telemetry: bool = True

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("profile must be a nonempty string")
        if type(self.require_telemetry) is not bool:
            raise ValueError("require_telemetry must be boolean")
        integer_fields = {
            "model_calls",
            "tool_calls",
            "tokens_in",
            "tokens_out",
            "steps",
        }
        for key, value in asdict(self).items():
            if key in {"schema_version", "profile", "require_telemetry"}:
                continue
            if value is None:
                if key in {"wall_clock_s", "sample_interval_s"}:
                    raise ValueError(f"{key} cannot be null")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{key} must be finite and positive, or null")
            if key in integer_fields and type(value) is not int:
                raise ValueError(f"{key} must be an integer")
        if not 0.01 <= self.sample_interval_s <= 1:
            raise ValueError("sample_interval_s must be between 0.01 and 1 second")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            raise ValueError("config must be an object")
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown configuration fields: {sorted(unknown)}")
        return cls(**data)

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
