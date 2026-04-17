from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class Position:
    code: str
    kind: str  # stock | etf | fund
    qty: float
    cost: float


@dataclass
class AnalysisRow:
    code: str
    name: str | None
    kind: str
    qty: float
    cost: float
    last_price: float | None
    chg_pct: float | None
    ma50: float | None
    ma200: float | None
    atr14: float | None
    drawdown: float | None
    signal: str
    action: str
    sell_qty: float
    buy_qty: float
    notes: str | None = None


@dataclass(frozen=True)
class MissingRealtime:
    code: str
    kind: str
    name: str | None
    reason: str


def _find_col(cols: list[str], keywords: list[str]) -> str | None:
    for c in cols:
        s = str(c)
        for k in keywords:
            if k in s:
                return c
    return None


def _to_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(str(x).strip())
    except Exception:
        return None


def _round2(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x), 2)


def _ma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / float(window)


def _atr14(high: list[float], low: list[float], close: list[float], window: int = 14) -> float | None:
    if len(close) < window + 1 or len(high) != len(low) or len(low) != len(close):
        return None
    trs: list[float] = []
    for i in range(1, len(close)):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        trs.append(tr)
    if len(trs) < window:
        return None
    return sum(trs[-window:]) / float(window)


def _stock_symbol_tx(code: str) -> str:
    # Tencent expects 'sh600519' / 'sz000001'
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    return f"sz{code}"


def _etf_symbol_sina(code: str) -> str:
    # Sina ETF hist expects 'sh510300' / 'sz159915'
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_stock_realtime_xq(code: str) -> tuple[str | None, float | None, float | None]:
    import akshare as ak  # type: ignore

    df = ak.stock_individual_spot_xq(symbol=("SH" + code if code.startswith(("6", "9", "5")) else "SZ" + code))
    # df columns: item / value
    m = {str(r["item"]).strip(): r["value"] for _, r in df.iterrows()}
    name = m.get("名称", None)
    last_price = _to_float(m.get("现价", m.get("最新价", m.get("最新", None))))
    chg_pct = _to_float(m.get("涨幅", m.get("涨跌幅", None)))
    return (str(name) if name is not None else None, last_price, chg_pct)


def fetch_stock_hist_tx(code: str, *, days: int = 260) -> tuple[list[float], list[float], list[float]]:
    import akshare as ak  # type: ignore

    end = date.today()
    start = end - timedelta(days=int(days * 2.2))
    df = ak.stock_zh_a_hist_tx(symbol=_stock_symbol_tx(code), start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    if df is None or len(df) == 0:
        return ([], [], [])
    close = [float(x) for x in df["close"].tolist()]
    high = [float(x) for x in df["high"].tolist()]
    low = [float(x) for x in df["low"].tolist()]
    return (close, high, low)


def fetch_etf_realtime_ths(code: str) -> tuple[str | None, float | None, float | None]:
    import akshare as ak  # type: ignore

    df = ak.fund_etf_spot_ths()
    if df is None or len(df) == 0:
        return (None, None, None)
    cols = [str(c) for c in df.columns]
    code_col = _find_col(cols, ["代码", "基金代码", "证券代码"])
    name_col = _find_col(cols, ["名称", "简称"])
    price_col = _find_col(cols, ["最新", "现价", "收盘", "净值"])
    chg_col = _find_col(cols, ["涨跌幅", "涨跌", "涨幅"])
    if code_col is None:
        return (None, None, None)
    row = df[df[code_col].astype(str) == str(code)]
    if len(row) == 0:
        return (None, None, None)
    r = row.iloc[0]
    name = str(r[name_col]) if name_col and r.get(name_col) is not None else None
    last_price = _to_float(r.get(price_col)) if price_col else None
    chg_pct = _to_float(r.get(chg_col)) if chg_col else None
    return (name, last_price, chg_pct)


def fetch_etf_hist_sina(code: str, *, days: int = 260) -> tuple[list[float], list[float], list[float]]:
    import akshare as ak  # type: ignore

    df = ak.fund_etf_hist_sina(symbol=_etf_symbol_sina(code))
    if df is None or len(df) == 0:
        return ([], [], [])
    close = [float(x) for x in df["close"].tolist()]
    high = [float(x) for x in df["high"].tolist()]
    low = [float(x) for x in df["low"].tolist()]
    return (close[-days:], high[-days:], low[-days:])


def fetch_fund_nav_em(code: str, *, days: int = 400) -> tuple[str | None, list[float]]:
    import akshare as ak  # type: ignore

    df = ak.fund_open_fund_info_em(symbol=str(code))
    if df is None or len(df) == 0:
        return (None, [])
    # columns are Chinese; rely on second column as unit nav
    nav_col = df.columns[1]
    nav = [float(x) for x in df[nav_col].tolist()]
    return (None, nav[-days:])


def decide_action(
    *,
    kind: str,
    qty: float,
    last_price: float | None,
    close_series: list[float],
    high_series: list[float] | None,
    low_series: list[float] | None,
    erp: float | None = None,
    greed_erp_threshold: float = 5.5,
    buy_pct: float = 0.1,
    buy_atr_k: float = 1.2,
    in_cooldown: bool = False,
    sell_pct_ma50: float = 0.2,
    sell_pct_ma200: float = 0.5,
    sell_pct_drawdown: float = 0.2,
    drawdown_atr_k: float = 2.0,
) -> tuple[str, str, float, dict[str, float | None]]:
    if not close_series:
        return ("NO_DATA", "观望", 0.0, {"ma50": None, "ma200": None, "atr14": None, "drawdown": None})

    close = close_series[-1]
    ma50 = _ma(close_series, 50)
    ma200 = _ma(close_series, 200)

    atr14 = None
    if high_series and low_series and len(high_series) == len(close_series):
        atr14 = _atr14(high_series, low_series, close_series, 14)
    else:
        # fallback ATR proxy for funds: mean abs diff
        if len(close_series) >= 15:
            diffs = [abs(close_series[i] - close_series[i - 1]) for i in range(1, len(close_series))]
            atr14 = sum(diffs[-14:]) / 14.0

    peak = max(close_series)
    drawdown = peak - close

    sell_pct = 0.0
    buy_pct_out = 0.0
    signal = "OK"
    action = "观望"

    if ma200 is not None and close < ma200:
        signal = "BELOW_MA200"
        action = "卖出"
        sell_pct = sell_pct_ma200
    elif ma50 is not None and close < ma50:
        signal = "BELOW_MA50"
        action = "卖出"
        sell_pct = sell_pct_ma50
    elif atr14 is not None and drawdown > drawdown_atr_k * atr14:
        signal = "DRAWDOWN_ATR"
        action = "卖出"
        sell_pct = sell_pct_drawdown
    else:
        # Buy rule (stage-1): only in "greed/undervalued" macro regime and not in cooldown
        if erp is not None and erp > greed_erp_threshold and (not in_cooldown) and atr14 is not None and drawdown > buy_atr_k * atr14:
            signal = "BUY_DRAWDOWN_ATR"
            action = "买入"
            buy_pct_out = buy_pct

    sell_qty = 0.0
    buy_qty = 0.0
    lot_note = ""
    if sell_pct > 0 and qty > 0:
        raw = qty * sell_pct
        if kind == "stock":
            sell_qty = float(int(raw // 100) * 100)
            if raw > 0 and sell_qty == 0:
                lot_note = "不足一手(100股)，建议手动处理"
        else:
            sell_qty = round(raw, 2)

    if buy_pct_out > 0 and qty > 0:
        raw = qty * buy_pct_out
        if kind == "stock":
            buy_qty = float(int(raw // 100) * 100)
            if raw > 0 and buy_qty == 0:
                lot_note = "不足一手(100股)，建议手动处理"
        else:
            buy_qty = round(raw, 2)

    if lot_note:
        signal = f"{signal}|LOT"
    return (
        signal,
        action,
        sell_qty,
        {"ma50": _round2(ma50), "ma200": _round2(ma200), "atr14": _round2(atr14), "drawdown": _round2(drawdown), "buy_qty": buy_qty},
    )


def analyze_positions(
    positions: list[Position],
    *,
    realtime_overrides: dict[str, tuple[str | None, float | None, float | None]] | None = None,
    erp: float | None = None,
    greed_erp_threshold: float = 5.5,
    buy_pct: float = 0.1,
    buy_atr_k: float = 1.2,
    cooldown_codes: set[str] | None = None,
) -> tuple[list[AnalysisRow], list[MissingRealtime]]:
    overrides = realtime_overrides or {}
    cooldown = cooldown_codes or set()
    rows: list[AnalysisRow] = []
    missing: list[MissingRealtime] = []

    for p in positions:
        name: str | None = None
        last_price: float | None = None
        chg_pct: float | None = None

        if p.code in overrides:
            name, last_price, chg_pct = overrides[p.code]

        close: list[float] = []
        high: list[float] = []
        low: list[float] = []

        try:
            if p.kind == "stock":
                if p.code not in overrides:
                    name, last_price, chg_pct = fetch_stock_realtime_xq(p.code)
                close, high, low = fetch_stock_hist_tx(p.code)
            elif p.kind == "etf":
                if p.code not in overrides:
                    name, last_price, chg_pct = fetch_etf_realtime_ths(p.code)
                close, high, low = fetch_etf_hist_sina(p.code)
            elif p.kind == "fund":
                # open-end fund: not truly intraday realtime; use latest NAV series
                name2, nav = fetch_fund_nav_em(p.code)
                name = name or name2
                close = nav
                high, low = [], []
            else:
                raise ValueError(f"unknown kind: {p.kind}")
        except Exception as e:  # noqa: BLE001
            rows.append(
                AnalysisRow(
                    code=p.code,
                    name=name,
                    kind=p.kind,
                    qty=p.qty,
                    cost=p.cost,
                    last_price=last_price,
                    chg_pct=chg_pct,
                    ma50=None,
                    ma200=None,
                    atr14=None,
                    drawdown=None,
                    signal="ERROR",
                    action="跳过",
                    sell_qty=0.0,
                    buy_qty=0.0,
                    notes=str(e),
                )
            )
            continue

        if (last_price is None or chg_pct is None) and p.kind in ("stock", "etf") and p.code not in overrides:
            missing.append(MissingRealtime(code=p.code, kind=p.kind, name=name, reason="缺少实时价格或涨跌幅"))

        signal, action, sell_qty, ind = decide_action(
            kind=p.kind,
            qty=p.qty,
            last_price=last_price,
            close_series=close,
            high_series=high if high else None,
            low_series=low if low else None,
            erp=erp,
            greed_erp_threshold=greed_erp_threshold,
            buy_pct=buy_pct,
            buy_atr_k=buy_atr_k,
            in_cooldown=(p.code in cooldown),
        )

        rows.append(
            AnalysisRow(
                code=p.code,
                name=name,
                kind=p.kind,
                qty=p.qty,
                cost=p.cost,
                last_price=_round2(last_price),
                chg_pct=_round2(chg_pct),
                ma50=ind["ma50"],
                ma200=ind["ma200"],
                atr14=ind["atr14"],
                drawdown=ind["drawdown"],
                signal=signal,
                action=action,
                sell_qty=sell_qty,
                buy_qty=float(ind.get("buy_qty") or 0.0),
                notes=(
                    "开放式基金净值非盘中实时"
                    if p.kind == "fund"
                    else ("不足一手(100股)，建议手动处理" if "LOT" in signal else None)
                ),
            )
        )

    return rows, missing

