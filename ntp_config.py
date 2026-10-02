from ClassWidgets.SDK import ConfigBaseModel
from pydantic import Field, field_validator


class NtpConfig(ConfigBaseModel):
    enabled: bool = False
    servers: list[str] = Field(default_factory=lambda: ["time.cloudflare.com", "ntp.aliyun.com", "pool.ntp.org"])
    interval_minutes: int = Field(default=30, ge=1, le=1440)
    timeout: float = Field(default=3, ge=0.1, le=15, allow_inf_nan=False)
    max_delay: float = Field(default=1, gt=0, le=10, allow_inf_nan=False)
    max_offset: float = Field(default=300, gt=0, le=86400, allow_inf_nan=False)
    compensation: float = Field(default=0, ge=-86400, le=86400, allow_inf_nan=False)
    restore_pending: bool = False
    original: int = 0
    last_written: int | None = None

    @field_validator("servers")
    @classmethod
    def validate_servers(cls, servers):
        values = list(dict.fromkeys(s.strip() for s in servers))
        if not 1 <= len(values) <= 8 or any(not s or len(s) > 253 or any(c.isspace() for c in s) or "/" in s for s in values):
            raise ValueError("请输入 1 至 8 个主机名或 IP 地址，不含端口、路径或空白")
        return values