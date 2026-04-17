from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FetchFailed(RuntimeError):
    metric: str
    missing_fields: tuple[str, ...]
    detail: str | None = None

    def __str__(self) -> str:
        base = f"FetchFailed(metric={self.metric}, missing_fields={list(self.missing_fields)})"
        if self.detail:
            return f"{base}: {self.detail}"
        return base

    @classmethod
    def from_missing(cls, metric: str, missing_fields: Iterable[str], detail: str | None = None) -> "FetchFailed":
        return cls(metric=metric, missing_fields=tuple(missing_fields), detail=detail)


class ValidationError(ValueError):
    pass

