from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, TypeVar

from config import AppConfig
from errors import FetchFailed
from models import ErpInputs

T = TypeVar("T")


def _retry(
    times: int,
    fn: Callable[[], T],
    *,
    sleep_seconds: float = 0.6,
    trace: Callable[[str], None] | None = None,
) -> T:
    last_exc: Exception | None = None
    for i in range(times):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - boundary retry wrapper
            last_exc = e
            if trace:
                trace(f"  尝试 {i + 1}/{times} 失败：{type(e).__name__}: {e}")
            if i < times - 1:
                wait_s = sleep_seconds * (i + 1)
                if trace:
                    trace(f"  {wait_s:.1f}s 后重试…")
                time.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


@dataclass(frozen=True)
class DataFetcher:
    config: AppConfig
    trace: Callable[[str], None] | None = None

    def _t(self, msg: str) -> None:
        if self.trace:
            self.trace(msg)

    def fetch_hs300_pe(self) -> float:
        """
        Fetch HS300 dynamic PE (TTM preferred).
        """

        def attempt() -> float:
            try:
                import akshare as ak  # type: ignore
            except Exception as e:  # noqa: BLE001
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail=f"AkShare not available: {e}",
                ) from e

            try:
                # LeguLegu: index PE time series.
                # In AkShare 1.18.55, this is exposed as stock_index_pe_lg().
                self._t("  调用 AkShare.stock_index_pe_lg() …")
                df = ak.stock_index_pe_lg()
            except Exception as e:  # noqa: BLE001
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail=f"stock_index_pe_lg failed: {e}",
                ) from e

            if df is None or len(df) == 0:
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail="stock_index_pe_lg returned empty data.",
                )

            # Expect columns like: 日期, 指数, ... 滚动市盈率, ...
            if "滚动市盈率" not in df.columns:
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail=f"stock_index_pe_lg missing expected column: 滚动市盈率. columns={list(df.columns)}",
                )

            # Choose latest non-null rolling PE
            series = df["滚动市盈率"].dropna()
            if len(series) == 0:
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail="stock_index_pe_lg returned no non-null 滚动市盈率 values.",
                )

            pe = float(series.iloc[-1])
            if pe <= 0:
                raise FetchFailed.from_missing(
                    metric="hs300_pe",
                    missing_fields=["hs300_pe"],
                    detail=f"Invalid 滚动市盈率 value: {pe}",
                )
            self._t(f"  stock_index_pe_lg 成功：最新滚动市盈率={pe}（有效行数≈{len(series)}）")
            return pe

        try:
            self._t(f"沪深300 PE：开始抓取（最多 {self.config.fetch_retries} 次尝试）…")
            return _retry(self.config.fetch_retries, attempt, trace=self.trace)
        except FetchFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchFailed.from_missing(metric="hs300_pe", missing_fields=["hs300_pe"], detail=str(e)) from e

    def fetch_bond_yield_10y(self) -> float:
        """
        Fetch China 10Y government bond yield (percent points, e.g. 2.35).
        """

        def attempt() -> float:
            try:
                import akshare as ak  # type: ignore
            except Exception as e:  # noqa: BLE001
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail=f"AkShare not available: {e}",
                ) from e

            try:
                self._t("  调用 AkShare.bond_china_yield() …")
                df = ak.bond_china_yield()
            except Exception as e:  # noqa: BLE001
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail=f"bond_china_yield failed: {e}",
                ) from e

            if df is None or len(df) == 0 or "10年" not in df.columns:
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail="bond_china_yield returned empty data or missing 10年 column.",
                )

            # We want the sovereign curve.
            if "曲线名称" in df.columns:
                df = df[df["曲线名称"] == "中债国债收益率曲线"]

            if len(df) == 0:
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail="bond_china_yield has no rows for 中债国债收益率曲线.",
                )

            # Pick latest by date if the column exists
            if "日期" in df.columns:
                try:
                    df = df.copy()
                    df["_dt"] = df["日期"].astype(str)
                    df = df.sort_values("_dt")
                except Exception:
                    pass

            series = df["10年"].dropna()
            if len(series) == 0:
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail="bond_china_yield returned no non-null 10年 values for sovereign curve.",
                )

            val = float(series.iloc[-1])
            if val < 0:
                raise FetchFailed.from_missing(
                    metric="bond_yield_10y",
                    missing_fields=["bond_yield_10y"],
                    detail="10Y yield is negative; treat as invalid for this tool.",
                )
            self._t(f"  bond_china_yield 成功：10年期收益率={val}%（有效行数≈{len(series)}）")
            return val

        try:
            self._t(f"10年期国债收益率：开始抓取（最多 {self.config.fetch_retries} 次尝试）…")
            return _retry(self.config.fetch_retries, attempt, trace=self.trace)
        except FetchFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchFailed.from_missing(metric="bond_yield_10y", missing_fields=["bond_yield_10y"], detail=str(e)) from e

    def fetch_erp_inputs(self) -> ErpInputs:
        """
        Fetch the minimal inputs needed for ERP computation.

        Behavior:
          - Try to fetch from AkShare (primary) with retry.
          - If fetching fails or returns invalid/empty values, raise FetchFailed so
            fallback_ui can ask the user for manual inputs.
        """

        def attempt() -> ErpInputs:
            missing: list[str] = []
            hs300_pe: float | None = None
            bond_10y: float | None = None

            try:
                hs300_pe = self.fetch_hs300_pe()
            except FetchFailed:
                missing.append("hs300_pe")

            try:
                bond_10y = self.fetch_bond_yield_10y()
            except FetchFailed:
                missing.append("bond_yield_10y")

            if missing:
                raise FetchFailed.from_missing(
                    metric="erp_inputs",
                    missing_fields=missing,
                    detail="AkShare fetch failed for one or more fields.",
                )

            assert hs300_pe is not None and bond_10y is not None
            return ErpInputs(hs300_pe=hs300_pe, bond_yield_10y=bond_10y)

        try:
            return _retry(self.config.fetch_retries, attempt)
        except FetchFailed:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchFailed.from_missing(
                metric="erp_inputs",
                missing_fields=["hs300_pe", "bond_yield_10y"],
                detail=str(e),
            ) from e

