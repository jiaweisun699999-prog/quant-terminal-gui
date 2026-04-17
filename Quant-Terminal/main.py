from __future__ import annotations

from datetime import date
import argparse
import sys
from dataclasses import replace
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import AppConfig, DEFAULT_CONFIG
from data_fetcher import DataFetcher
from errors import FetchFailed
from fallback_ui import collect_manual_inputs
from models import AppState, DcaPlan, ErpInputs
from storage import Storage
from strategy import ErpEngine, ProfitDripSystem, TimeInflationPrincipal


def _format_currency(x: float) -> str:
    return f"{x:,.2f}"

def _iso_week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)

def _should_release_drip(as_of: date, last_release: date | None) -> bool:
    # Wednesday = 2 (Mon=0)
    if as_of.weekday() != 2:
        return False
    if last_release is None:
        return True
    return _iso_week_key(as_of) != _iso_week_key(last_release)

def build_weekly_plan(
    as_of: date,
    *,
    config: AppConfig = DEFAULT_CONFIG,
    take_profit_amount: float | None = None,
    allow_manual_fallback: bool = True,
    hs300_pe_override: float | None = None,
    bond_yield_10y_override: float | None = None,
    year_override: int | None = None,
    trace_log: Callable[[str], None] | None = None,
) -> tuple[DcaPlan, AppState]:
    def _lg(msg: str) -> None:
        if trace_log is not None:
            trace_log(msg)

    year = int(year_override) if year_override is not None else as_of.year
    take_profit_amount = float(take_profit_amount) if take_profit_amount is not None else 0.0

    frozen = getattr(sys, "frozen", False)
    _lg(
        "build_weekly_plan 开始："
        f"as_of={as_of.isoformat()}, take_profit={take_profit_amount}, "
        f"year={year}, year_override={year_override!r}, hs300_pe_override={hs300_pe_override!r}, "
        f"bond_yield_10y_override={bond_yield_10y_override!r}, allow_manual_fallback={allow_manual_fallback}, "
        f"frozen={frozen}, fetch_retries={config.fetch_retries}"
    )

    storage = Storage(config)
    state = storage.load_state()
    _lg("已读取本地账本 state（滴灌/最高净值等）。")

    fetcher = DataFetcher(config, trace=trace_log)
    if trace_log is not None:
        try:
            import akshare as ak  # type: ignore

            _lg(f"AkShare 版本：{getattr(ak, '__version__', 'unknown')}")
        except Exception as e:  # noqa: BLE001
            _lg(f"导入 AkShare 以读取版本失败：{type(e).__name__}: {e}")
    sources: dict[str, str] = {}
    notes: list[str] = []

    hs300_pe: float | None = None
    bond_yield_10y: float | None = None
    missing: set[str] = set()

    if hs300_pe_override is not None:
        hs300_pe = float(hs300_pe_override)
        sources["hs300_pe"] = "manual"
        _lg(f"沪深300 PE 使用手工覆盖值：{hs300_pe}")
    else:
        try:
            hs300_pe = fetcher.fetch_hs300_pe()
            sources["hs300_pe"] = "api"
        except FetchFailed as e:
            missing.add("hs300_pe")
            _lg(f"沪深300 PE 抓取失败（将进入缺失集合）：{type(e).__name__} detail={e.detail!r}")
            notes.append(f"沪深300 PE 抓取失败，将手工输入。原因：{e.detail or str(e)}")

    if bond_yield_10y_override is not None:
        bond_yield_10y = float(bond_yield_10y_override)
        sources["bond_yield_10y"] = "manual"
        _lg(f"10年期国债收益率使用手工覆盖值：{bond_yield_10y}")
    else:
        try:
            bond_yield_10y = fetcher.fetch_bond_yield_10y()
            sources["bond_yield_10y"] = "api"
        except FetchFailed as e:
            missing.add("bond_yield_10y")
            _lg(f"10年期国债收益率抓取失败（将进入缺失集合）：{type(e).__name__} detail={e.detail!r}")
            notes.append(f"10年期国债收益率抓取失败，将手工输入。原因：{e.detail or str(e)}")

    if missing:
        _lg(f"ERP 输入缺失字段：{sorted(missing)}；allow_manual_fallback={allow_manual_fallback}")
        if not allow_manual_fallback:
            err = FetchFailed.from_missing(metric="erp_inputs", missing_fields=sorted(missing))
            _lg(f"抛出 FetchFailed（无 CLI/UI 兜底）：missing_fields={err.missing_fields!r} detail={err.detail!r}")
            raise err

        defaults = None
        if hs300_pe is not None or bond_yield_10y is not None:
            defaults = ErpInputs(hs300_pe=(hs300_pe or 0.0), bond_yield_10y=(bond_yield_10y or 0.0))

        manual = collect_manual_inputs(
            as_of=as_of,
            fetch_error=FetchFailed.from_missing(metric="erp_inputs", missing_fields=sorted(missing)),
            missing_fields=missing,
            defaults=defaults,
        )
        year = manual.year
        erp_inputs = manual.erp_inputs
        take_profit_amount = manual.take_profit_amount
        sources.update(manual.sources)
        _lg("已通过 CLI/UI 手工兜底补齐 ERP 输入。")
    else:
        erp_inputs = ErpInputs(hs300_pe=float(hs300_pe), bond_yield_10y=float(bond_yield_10y))
        sources.update({"year": "manual" if year_override is not None else "api", "take_profit_amount": "manual"})
        notes.append("ERP 指标来自 AkShare 数据源；止盈利润默认为 0（可手工补录）。")
        _lg(f"ERP 原始输入齐全：hs300_pe={erp_inputs.hs300_pe}, bond_yield_10y={erp_inputs.bond_yield_10y}%")

    # --- Strategy: base principal (time inflation) ---
    principal = TimeInflationPrincipal(config)
    weekly_base = principal.weekly_base_for_year(year)
    _lg(f"时间通胀本金：year={year} → 盾周基数={weekly_base.shield_base:.2f}, 矛周基数={weekly_base.spear_base:.2f}")

    # --- Strategy: ERP multiplier ---
    erp_engine = ErpEngine(config)
    erp_result = erp_engine.compute(erp_inputs)
    _lg(f"ERP 计算结果：erp={erp_result.erp:.4f}%, multiplier={erp_result.multiplier:.4f}x")

    # --- Strategy: profit drip (applies to spear amount by default) ---
    drip_system = ProfitDripSystem(config)
    drip_state_after_add = drip_system.add_take_profit(state.drip, take_profit_amount)
    if _should_release_drip(as_of, state.last_drip_release_date):
        drip_amount, drip_state_after_release = drip_system.release_this_week(drip_state_after_add)
        last_drip_release_date = as_of
    else:
        drip_amount, drip_state_after_release = 0.0, drip_state_after_add
        last_drip_release_date = state.last_drip_release_date
        notes.append("滴灌仅在周三且每周只释放一次；本次运行不释放滴灌。")

    # Final suggested amounts
    shield_amount = round(weekly_base.shield_base * erp_result.multiplier, 2)
    spear_amount = round(weekly_base.spear_base * erp_result.multiplier + drip_amount, 2)
    _lg(f"本周建议定投：盾={shield_amount:.2f}, 矛={spear_amount:.2f}, 滴灌释放={drip_amount:.2f}")

    if take_profit_amount > 0:
        notes.append(f"本周新增止盈利润 {_format_currency(take_profit_amount)}，将滴灌分摊 {config.drip_weeks} 周。")
    if drip_amount > 0:
        notes.append(f"本周滴灌释放 {_format_currency(drip_amount)}（默认计入「矛」定投额）。")

    plan = DcaPlan(
        as_of=as_of,
        year=year,
        erp=erp_result.erp,
        erp_multiplier=erp_result.multiplier,
        shield_amount=shield_amount,
        spear_amount=spear_amount,
        drip_amount=drip_amount,
        sources={k: v for k, v in sources.items()},  # type: ignore[assignment]
        notes=notes,
    )

    new_state = AppState(
        drip=drip_state_after_release,
        highest_nav=state.highest_nav,
        last_shot_date=state.last_shot_date,
        last_drip_release_date=last_drip_release_date,
    )
    storage.save_state(new_state)
    _lg("已写入 state 与账本 ledger。")
    storage.append_ledger(
        {
            "meta": {
                "app_version": config.app_version,
                "strategy_version": config.strategy_version,
            },
            "inputs": {
                "as_of": as_of.isoformat(),
                "year": year,
                "hs300_pe": float(erp_inputs.hs300_pe),
                "bond_yield_10y": float(erp_inputs.bond_yield_10y),
                "take_profit_amount": float(take_profit_amount),
                "sources": dict(sources),
            },
            "plan": plan.to_jsonable(),
            "state": new_state.to_jsonable(),
        }
    )

    _lg("build_weekly_plan 完成。")
    return plan, new_state


def render_report(plan: DcaPlan) -> None:
    console = Console()

    headline = Text(f"Quant-Terminal Weekly Report\nDate: {plan.as_of.isoformat()}")
    console.print(Panel.fit(headline, title="战报", border_style="cyan"))

    t = Table(title="核心指标", show_lines=False)
    t.add_column("Item", style="bold")
    t.add_column("Value", justify="right")

    t.add_row("ERP", f"{plan.erp:.4f}%")
    t.add_row("ERP Multiplier", f"{plan.erp_multiplier:.2f}x")
    t.add_row("Drip (this week)", _format_currency(plan.drip_amount))
    console.print(t)

    t2 = Table(title="本周建议定投", show_lines=False)
    t2.add_column("Bucket", style="bold")
    t2.add_column("Amount", justify="right")
    t2.add_row("盾 (Shield)", _format_currency(plan.shield_amount))
    t2.add_row("矛 (Spear)", _format_currency(plan.spear_amount))
    console.print(t2)

    if plan.sources:
        ts = Table(title="数据来源", show_lines=False)
        ts.add_column("Field", style="bold")
        ts.add_column("Source", justify="right")
        for k in sorted(plan.sources.keys()):
            ts.add_row(k, str(plan.sources[k]))
        console.print(ts)

    if plan.notes:
        notes_text = Text("Notes\n" + "\n".join([f"- {n}" for n in plan.notes]))
        console.print(Panel(notes_text, border_style="green"))


def main() -> None:
    p = argparse.ArgumentParser(prog="quant-terminal")
    p.add_argument("--date", dest="as_of", default="", help="Run date in YYYY-MM-DD (default: today)")
    p.add_argument("--take-profit", dest="take_profit", default=None, help="Take-profit amount to add into drip queue (number)")
    args = p.parse_args()

    as_of = date.today() if not args.as_of else date.fromisoformat(args.as_of)
    tp = None if args.take_profit is None else float(args.take_profit)

    plan, _state = build_weekly_plan(as_of=as_of, take_profit_amount=tp)
    render_report(plan)


if __name__ == "__main__":
    main()

