from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from errors import FetchFailed
from models import DataSource, ErpInputs


def _parse_float(raw: str, *, allow_percent: bool) -> float:
    s = raw.strip()
    if allow_percent and s.endswith("%"):
        s = s[:-1].strip()
    return float(s)


def prompt_float(
    prompt: str,
    *,
    allow_percent: bool = False,
    min_value: float | None = None,
    max_value: float | None = None,
    default: float | None = None,
) -> float:
    while True:
        suffix = ""
        if default is not None:
            suffix = f" [default: {default}]"
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw == "" and default is not None:
            return float(default)
        try:
            val = _parse_float(raw, allow_percent=allow_percent)
        except Exception:
            print("输入无法解析为数字，请重试。示例：3.25 或 3.25%")
            continue

        if min_value is not None and val < min_value:
            print(f"输入过小，需 >= {min_value}")
            continue
        if max_value is not None and val > max_value:
            print(f"输入过大，需 <= {max_value}")
            continue
        return float(val)


def prompt_int(prompt: str, *, min_value: int | None = None, max_value: int | None = None, default: int | None = None) -> int:
    while True:
        suffix = ""
        if default is not None:
            suffix = f" [default: {default}]"
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw == "" and default is not None:
            return int(default)
        try:
            val = int(raw)
        except Exception:
            print("输入无法解析为整数，请重试。")
            continue
        if min_value is not None and val < min_value:
            print(f"输入过小，需 >= {min_value}")
            continue
        if max_value is not None and val > max_value:
            print(f"输入过大，需 <= {max_value}")
            continue
        return int(val)


@dataclass(frozen=True)
class ManualInputs:
    year: int
    erp_inputs: ErpInputs
    take_profit_amount: float
    sources: dict[str, DataSource]


def collect_manual_inputs(
    as_of: date,
    fetch_error: FetchFailed | None = None,
    *,
    missing_fields: set[str] | None = None,
    defaults: ErpInputs | None = None,
) -> ManualInputs:
    if fetch_error is not None:
        print(f"\n[提示] 数据抓取失败：{fetch_error}")
        print("将进入手工输入模式，确保策略计算不中断。\n")

    year = prompt_int("请输入年份", min_value=2000, max_value=3000, default=as_of.year)
    need = missing_fields or {"hs300_pe", "bond_yield_10y"}

    if "hs300_pe" in need:
        hs300_pe = prompt_float("请输入沪深300 动态PE（例如 12.8）", min_value=0.0001, default=(defaults.hs300_pe if defaults else None))
    else:
        assert defaults is not None
        hs300_pe = float(defaults.hs300_pe)

    if "bond_yield_10y" in need:
        bond_yield = prompt_float(
            "请输入10年期国债收益率（例如 2.35 或 2.35%）",
            allow_percent=True,
            min_value=0.0,
            max_value=20.0,
            default=(defaults.bond_yield_10y if defaults else None),
        )
    else:
        assert defaults is not None
        bond_yield = float(defaults.bond_yield_10y)

    take_profit = prompt_float("本周止盈利润金额（没有则填 0）", min_value=0.0, default=0.0)

    sources: dict[str, DataSource] = {
        "year": "manual",
        "hs300_pe": "manual" if "hs300_pe" in need else "api",
        "bond_yield_10y": "manual" if "bond_yield_10y" in need else "api",
        "take_profit_amount": "manual",
    }
    return ManualInputs(
        year=year,
        erp_inputs=ErpInputs(hs300_pe=hs300_pe, bond_yield_10y=bond_yield),
        take_profit_amount=take_profit,
        sources=sources,
    )

