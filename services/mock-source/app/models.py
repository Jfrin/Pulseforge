from pydantic import BaseModel, Field, model_validator


class Chaos(BaseModel):
    """Failure injection. error_rate + rate_limit_rate are one dice roll, so they must sum to <= 1."""

    error_rate: float = Field(default=0, ge=0, le=1)  # share of requests that return 500
    rate_limit_rate: float = Field(default=0, ge=0, le=1)  # share of requests that return 429 + Retry-After
    duplicate_rate: float = Field(default=0, ge=0, le=1)  # share of pages that repeat one record
    latency_ms: int = Field(default=0, ge=0, le=10_000)
    retry_after_s: int = Field(default=2, ge=0, le=60)

    @model_validator(mode="after")
    def _rates_fit(self):
        if self.error_rate + self.rate_limit_rate > 1:
            raise ValueError("error_rate + rate_limit_rate must be <= 1")
        return self


class ReviewBomb(BaseModel):
    game_id: str
    count: int = Field(default=150, ge=1, le=5000)
    negative_share: float = Field(0.9, ge=0, le=1)
    spread_minutes: int = Field(default=30, ge=1, le=1440)
