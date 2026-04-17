from __future__ import annotations

from datetime import date

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, Static, TextArea

import main as qt_main


class QuantTerminalApp(App):
    CSS = """
    Screen { padding: 1; }
    #root { height: 100%; }
    #controls { height: auto; }
    #report { height: 1fr; }
    .row { height: auto; }
    Input { width: 1fr; }
    """

    BINDINGS = [
        ("ctrl+r", "run", "运行"),
        ("ctrl+q", "quit", "退出"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="root"):
            with Horizontal(id="controls"):
                yield Label("日期（YYYY-MM-DD，可空）")
                yield Input(placeholder="留空表示今天", id="as_of")
                yield Label("止盈利润（进入滴灌，可空）")
                yield Input(value="0", id="take_profit")
                yield Button("运行", id="run", variant="primary")
            yield Static("就绪。", id="status")
            yield TextArea("", id="report", read_only=True)
        yield Footer()

    def _parse_date(self, raw: str) -> date:
        raw = raw.strip()
        if not raw:
            return date.today()
        return date.fromisoformat(raw)

    def _parse_float(self, raw: str) -> float:
        s = raw.strip()
        if not s:
            return 0.0
        return float(s)

    def action_run(self) -> None:
        self._run_once()

    @on(Button.Pressed, "#run")
    def on_run_pressed(self) -> None:
        self._run_once()

    def _run_once(self) -> None:
        status = self.query_one("#status", Static)
        report = self.query_one("#report", TextArea)
        as_of_in = self.query_one("#as_of", Input)
        tp_in = self.query_one("#take_profit", Input)

        try:
            as_of = self._parse_date(as_of_in.value)
        except Exception as e:  # noqa: BLE001
            status.update(f"日期格式错误：{e}")
            return

        try:
            take_profit_amount = self._parse_float(tp_in.value)
            if take_profit_amount < 0:
                raise ValueError("must be >= 0")
        except Exception as e:  # noqa: BLE001
            status.update(f"止盈利润输入错误：{e}")
            return

        status.update("正在运行...")
        try:
            plan, _state = qt_main.build_weekly_plan(
                as_of=as_of,
                take_profit_amount=take_profit_amount,
                allow_manual_fallback=False,
            )
        except Exception as e:  # noqa: BLE001
            status.update(f"运行失败（可能缺少数据）。可稍后再试，或使用 CLI 手工回退：{e}")
            return

        lines: list[str] = []
        lines.append("Quant-Terminal 战报")
        lines.append(f"日期：{plan.as_of.isoformat()}")
        lines.append("")
        lines.append("核心指标")
        lines.append(f"- ERP：{plan.erp:.4f}%")
        lines.append(f"- ERP 倍率：{plan.erp_multiplier:.2f}x")
        lines.append(f"- 本周滴灌释放：{plan.drip_amount:,.2f}")
        lines.append("")
        lines.append("本周建议定投")
        lines.append(f"- 盾：{plan.shield_amount:,.2f}")
        lines.append(f"- 矛：{plan.spear_amount:,.2f}")
        lines.append("")
        if plan.sources:
            lines.append("数据来源")
            for k in sorted(plan.sources.keys()):
                lines.append(f"- {k}: {plan.sources[k]}")
            lines.append("")
        if plan.notes:
            lines.append("备注")
            for n in plan.notes:
                lines.append(f"- {n}")

        report.text = "\n".join(lines)
        status.update("完成。（已写入 SQLite 账本）")


if __name__ == "__main__":
    QuantTerminalApp().run()

