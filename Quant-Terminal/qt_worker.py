from __future__ import annotations

import socket
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from config import AppConfig, DEFAULT_CONFIG
from errors import FetchFailed
from portfolio_analysis import MissingRealtime, Position, analyze_positions
from storage import Storage
import main as qt_main


@dataclass(frozen=True)
class RunParams:
    as_of: date
    take_profit_amount: float
    config: AppConfig = DEFAULT_CONFIG
    # optional manual overrides for ERP inputs (from fallback dialog)
    year_override: int | None = None
    hs300_pe_override: float | None = None
    bond_yield_10y_override: float | None = None
    # optional realtime overrides per code
    realtime_overrides: dict[str, tuple[str | None, float | None, float | None]] | None = None


class RunWorker(QObject):
    log = Signal(str)
    progress = Signal(int, int, str)  # current, total, message
    plan_ready = Signal(object)  # DcaPlan
    sources_ready = Signal(dict)  # sources map
    positions_ready = Signal(object)  # list[AnalysisRow]
    need_erp_fallback = Signal(object)  # FetchFailed
    need_realtime_overrides = Signal(object)  # list[MissingRealtime]
    finished = Signal()
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, params: RunParams) -> None:
        super().__init__()
        self.params = params
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def _call_with_timeout(
        self,
        fn,
        *,
        timeout_s: float,
        poll_s: float = 0.2,
        trace: Callable[[str], None] | None = None,
        stage: str = "任务",
    ):
        """
        Run a potentially blocking function in a separate Python thread with a soft timeout.
        Important: on timeout we DO NOT wait for the thread to finish (otherwise timeout is useless).
        """
        if trace:
            trace(f"{stage}：在线程池中执行（软超时 {timeout_s:g}s）…")
        ex = ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(fn)
        start = time.monotonic()
        try:
            while True:
                if self._cancel:
                    raise KeyboardInterrupt("cancelled")
                try:
                    out = fut.result(timeout=poll_s)
                    if trace:
                        trace(f"{stage}：结束，耗时 {time.monotonic() - start:.2f}s")
                    return out
                except TimeoutError:
                    if (time.monotonic() - start) >= float(timeout_s):
                        if trace:
                            trace(f"{stage}：已超过软超时 {timeout_s:g}s（工作线程未等待其退出）。")
                        raise TimeoutError(f"timeout after {timeout_s}s")
        finally:
            # Do not wait here; we want UI to proceed even if underlying lib is stuck.
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    def run(self) -> None:
        try:
            socket.setdefaulttimeout(12)
        except Exception:
            pass

        try:
            self._run_impl()
        except KeyboardInterrupt:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001
            self.log.emit(f"未捕获异常：{type(e).__name__}: {e}")
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(e))

    def _run_impl(self) -> None:
        self.log.emit("开始运行：抓取 ERP 指标…")
        if self._cancel:
            self.cancelled.emit()
            return

        self.log.emit(
            f"[参数] as_of={self.params.as_of.isoformat()}, take_profit={self.params.take_profit_amount}, "
            f"year_override={self.params.year_override!r}, hs300_pe_override={self.params.hs300_pe_override!r}, "
            f"bond_yield_10y_override={self.params.bond_yield_10y_override!r}, "
            f"实时覆盖条目数={len(self.params.realtime_overrides or {})}"
        )

        # Same path for script and PyInstaller exe: ERP in a worker thread with soft timeout
        # (matches `python qt_app.py`; avoids subprocess/stdout issues under onefile+noconsole).
        def _erp_task():
            return qt_main.build_weekly_plan(
                as_of=self.params.as_of,
                config=self.params.config,
                take_profit_amount=self.params.take_profit_amount,
                allow_manual_fallback=False,
                year_override=self.params.year_override,
                hs300_pe_override=self.params.hs300_pe_override,
                bond_yield_10y_override=self.params.bond_yield_10y_override,
                trace_log=self.log.emit,
            )

        erp_timeout = float(getattr(self.params.config, "erp_build_timeout_s", 60.0) or 60.0)
        self.log.emit(f"ERP 阶段软超时配置：{erp_timeout:g}s（慢网络/重试多时可到「设置」调大）")
        try:
            plan, _state = self._call_with_timeout(
                _erp_task, timeout_s=erp_timeout, trace=self.log.emit, stage="ERP(build_weekly_plan)"
            )
        except FetchFailed as e:
            mf = getattr(e, "missing_fields", None)
            self.log.emit(
                f"ERP 阶段 FetchFailed：metric={e.metric!r} missing_fields={mf!r} detail={e.detail!r}"
            )
            self.need_erp_fallback.emit(e)
            return
        except TimeoutError:
            self.log.emit("ERP 阶段：线程池软超时，将打开手工回退。")
            self.need_erp_fallback.emit(
                FetchFailed.from_missing(
                    metric="erp_inputs",
                    missing_fields=["hs300_pe", "bond_yield_10y"],
                    detail="ERP 抓取超时，请手工补齐。",
                )
            )
            return

        self.plan_ready.emit(plan)
        self.sources_ready.emit({k: str(v) for k, v in plan.sources.items()})
        self.log.emit("ERP 完成，开始分析持仓…")

        # Portfolio positions
        storage = Storage(self.params.config)
        pf = storage.list_portfolio()
        positions = [Position(code=r["code"], kind=r["kind"], qty=r["qty"], cost=r["cost"]) for r in pf]
        self.log.emit(f"持仓列表：共 {len(positions)} 条。")

        if not positions:
            self.positions_ready.emit([])
            self.finished.emit()
            return

        # Determine cooldown codes
        cooldown: set[str] = set()
        for r in pf:
            iso = storage.get_last_buy_date(r["code"])
            if not iso:
                continue
            try:
                last = date.fromisoformat(str(iso))
            except Exception:
                continue
            if (self.params.as_of - last).days < 3:
                cooldown.add(r["code"])

        if cooldown:
            self.log.emit(f"买入冷却（<3 天）标的：{sorted(cooldown)}")

        overrides = dict(self.params.realtime_overrides or {})
        if overrides:
            self.log.emit(f"已应用实时行情手工覆盖：{list(overrides.keys())}")

        total = len(positions)
        results: list[Any] = []
        missing_all: list[MissingRealtime] = []

        # Analyze each position with a soft timeout, so one slow endpoint doesn't hang forever.
        per_symbol_timeout_s = 20
        for idx, p in enumerate(positions, start=1):
            if self._cancel:
                self.cancelled.emit()
                return

            self.progress.emit(idx - 1, total, f"分析 {p.code}…")
            self.log.emit(f"[{idx}/{total}] 分析 {p.code}（{p.kind}）…")

            def _task():
                rows, missing = analyze_positions(
                    [p],
                    realtime_overrides=overrides,
                    erp=getattr(plan, "erp", None),
                    greed_erp_threshold=self.params.config.erp_greed_threshold,
                    buy_pct=0.1,
                    buy_atr_k=1.2,
                    cooldown_codes=cooldown,
                )
                return rows, missing

            try:
                rows, miss = self._call_with_timeout(
                    _task,
                    timeout_s=float(per_symbol_timeout_s),
                    trace=self.log.emit,
                    stage=f"持仓[{p.code}]",
                )
            except TimeoutError:
                self.log.emit(f"[{idx}/{total}] {p.code} 超时（>{per_symbol_timeout_s}s），已跳过，可稍后重试或手工补齐。")
                continue

            results.extend(rows)
            missing_all.extend(miss)
            if miss:
                self.log.emit(
                    f"[{idx}/{total}] {p.code} 缺少实时数据："
                    + "; ".join(f"{m.code}({m.kind}): {m.reason}" for m in miss)
                )
            else:
                self.log.emit(f"[{idx}/{total}] {p.code} 分析完成，输出 {len(rows)} 行。")

        # If realtime missing exists, ask UI to prompt once, then rerun.
        if missing_all:
            self.log.emit(
                "需要手工补齐实时行情："
                + "; ".join(f"{m.code}({m.kind}) {m.reason}" for m in missing_all)
            )
            self.need_realtime_overrides.emit(missing_all)
            return

        self.log.emit(f"持仓分析全部完成，合并行数={len(results)}。")
        self.positions_ready.emit(results)
        self.progress.emit(total, total, "完成")
        self.finished.emit()

