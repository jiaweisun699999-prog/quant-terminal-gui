from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal


DataSource = Literal["api", "manual", "mixed"]


@dataclass(frozen=True)
class ErpInputs:
    hs300_pe: float  # dynamic PE for HS300
    bond_yield_10y: float  # 10Y bond yield, in percent points (e.g. 2.35 means 2.35%)


@dataclass(frozen=True)
class ErpResult:
    erp: float  # percent points
    multiplier: float


@dataclass(frozen=True)
class WeeklyBasePlan:
    year: int
    shield_base: float
    spear_base: float


@dataclass(frozen=True)
class DcaPlan:
    as_of: date
    year: int
    erp: float
    erp_multiplier: float
    shield_amount: float
    spear_amount: float
    drip_amount: float
    sources: dict[str, DataSource] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        return payload

    @classmethod
    def from_jsonable(cls, obj: dict[str, Any]) -> "DcaPlan":
        return cls(
            as_of=date.fromisoformat(str(obj["as_of"])),
            year=int(obj["year"]),
            erp=float(obj["erp"]),
            erp_multiplier=float(obj["erp_multiplier"]),
            shield_amount=float(obj["shield_amount"]),
            spear_amount=float(obj["spear_amount"]),
            drip_amount=float(obj["drip_amount"]),
            sources=dict(obj.get("sources") or {}),
            notes=list(obj.get("notes") or []),
        )


@dataclass
class DripState:
    """
    A fixed-length queue of future weekly drip amounts.
    queue[0] is the amount to release for the current week.
    """

    weeks: int
    queue: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.weeks <= 0:
            raise ValueError("DripState.weeks must be > 0")
        if not self.queue:
            self.queue = [0.0 for _ in range(self.weeks)]
        if len(self.queue) != self.weeks:
            raise ValueError(f"DripState.queue length must be {self.weeks}, got {len(self.queue)}")

    def to_jsonable(self) -> dict[str, Any]:
        return {"weeks": self.weeks, "queue": list(self.queue)}

    @classmethod
    def from_jsonable(cls, obj: dict[str, Any]) -> "DripState":
        weeks = int(obj["weeks"])
        queue = [float(x) for x in obj["queue"]]
        return cls(weeks=weeks, queue=queue)


@dataclass
class AppState:
    drip: DripState
    highest_nav: float | None = None
    last_shot_date: date | None = None
    last_drip_release_date: date | None = None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "drip": self.drip.to_jsonable(),
            "highest_nav": self.highest_nav,
            "last_shot_date": self.last_shot_date.isoformat() if self.last_shot_date else None,
            "last_drip_release_date": self.last_drip_release_date.isoformat() if self.last_drip_release_date else None,
        }

    @classmethod
    def from_jsonable(cls, obj: dict[str, Any]) -> "AppState":
        drip = DripState.from_jsonable(obj["drip"])
        highest_nav = obj.get("highest_nav", None)
        last_shot_date = obj.get("last_shot_date", None)
        last_drip_release_date = obj.get("last_drip_release_date", None)
        return cls(
            drip=drip,
            highest_nav=float(highest_nav) if highest_nav is not None else None,
            last_shot_date=date.fromisoformat(last_shot_date) if last_shot_date else None,
            last_drip_release_date=date.fromisoformat(last_drip_release_date) if last_drip_release_date else None,
        )

