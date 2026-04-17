from __future__ import annotations

import io
import os
import sys
from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QThread, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_CONFIG, AppConfig
from errors import FetchFailed
from qt_fallback_dialog import ManualFallbackDialog
from qt_realtime_dialog import RealtimeOverrideDialog
from storage import Storage
from qt_worker import RunParams, RunWorker


def _fmt_money(x: float) -> str:
    return f"{x:,.2f}"


def _safe_float(s: str, default: float) -> float:
    try:
        return float(s)
    except Exception:
        return default


def _safe_int(s: str, default: int) -> int:
    try:
        return int(float(s))
    except Exception:
        return default


def _configure_frozen_runtime() -> None:
    """PyInstaller frozen app: set CA bundle so HTTPS (AkShare/requests) can verify certificates."""
    if not getattr(sys, "frozen", False):
        return
    # --noconsole 下 stdout/stderr 为 None，AkShare 内 tqdm 等会 AttributeError: 'NoneType' has no attribute 'write'
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    os.environ.setdefault("TQDM_DISABLE", "1")
    try:
        import certifi

        ca = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)
    except Exception:
        pass


SETTINGS_FIELDS: list[tuple[str, str, str]] = [
    ("base_year", "基准年份", "int"),
    ("annual_principal_growth_rate", "年复利增长率(例如 0.15)", "float"),
    ("shield_weekly_base", "盾：基准周定投额", "float"),
    ("spear_weekly_base", "矛：基准周定投额", "float"),
    ("erp_greed_threshold", "ERP 贪婪阈值(%)", "float"),
    ("erp_fear_threshold", "ERP 恐惧阈值(%)", "float"),
    ("erp_greed_multiplier", "贪婪倍率", "float"),
    ("erp_fear_multiplier", "恐惧倍率", "float"),
    ("erp_neutral_multiplier", "中性倍率", "float"),
    ("drip_weeks", "滴灌周数", "int"),
    ("erp_build_timeout_s", "ERP 阶段软超时(秒)", "float"),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quant-Terminal（量化终端）")
        self.resize(1050, 760)

        self.storage = Storage(DEFAULT_CONFIG)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.run_tab = self._build_run_tab()
        self.history_tab = self._build_history_tab()
        self.settings_tab = self._build_settings_tab()
        self.watchlist_tab = self._build_watchlist_tab()
        self.portfolio_tab = self._build_portfolio_tab()

        self.tabs.addTab(self.run_tab, "运行")
        self.tabs.addTab(self.history_tab, "历史")
        self.tabs.addTab(self.settings_tab, "设置")
        self.tabs.addTab(self.watchlist_tab, "自选")
        self.tabs.addTab(self.portfolio_tab, "持仓")

        self.refresh_all()
        self._run_thread: QThread | None = None
        self._run_worker: RunWorker | None = None
        self._year_override: int | None = None
        self._hs300_pe_override: float | None = None
        self._bond_yield_10y_override: float | None = None
        self._realtime_overrides: dict[str, tuple[str | None, float | None, float | None]] = {}

    # ---------------------------
    # Settings: load/apply
    # ---------------------------
    def current_config(self) -> AppConfig:
        cfg = DEFAULT_CONFIG
        for key, _label, typ in SETTINGS_FIELDS:
            raw = self.storage.get_setting(key, None)
            if raw is None or raw == "":
                continue
            if typ == "int":
                cfg = replace(cfg, **{key: _safe_int(raw, getattr(cfg, key))})
            else:
                cfg = replace(cfg, **{key: _safe_float(raw, getattr(cfg, key))})
        return cfg

    def refresh_all(self) -> None:
        self.refresh_settings_form()
        self.refresh_history()
        self.refresh_watchlist()
        self.refresh_portfolio()

    # ---------------------------
    # Run tab
    # ---------------------------
    def _build_run_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        controls = QGroupBox("运行")
        form = QFormLayout(controls)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("运行日期", self.date_edit)

        self.take_profit = QDoubleSpinBox()
        self.take_profit.setRange(0.0, 1e12)
        self.take_profit.setDecimals(2)
        self.take_profit.setSingleStep(100.0)
        form.addRow("本次止盈利润（进入滴灌）", self.take_profit)

        self.force_fallback = QCheckBox("强制弹出手工回退弹窗（测试用）")
        form.addRow("", self.force_fallback)

        row = QHBoxLayout()
        self.run_btn = QPushButton("运行")
        self.run_btn.clicked.connect(self.on_run_clicked)
        row.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)
        row.addWidget(self.cancel_btn)
        self.force_stop_btn = QPushButton("强制停止")
        self.force_stop_btn.setEnabled(False)
        self.force_stop_btn.clicked.connect(self.on_force_stop_clicked)
        row.addWidget(self.force_stop_btn)
        self.status = QLabel("就绪。")
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.status, 1)
        form.addRow(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        form.addRow("进度", self.progress)

        layout.addWidget(controls)

        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("战报会显示在这里。")
        layout.addWidget(self.report, 1)

        self.log_panel = QTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setPlaceholderText("运行日志会显示在这里（可用于判断是否卡住/卡在哪一步）。")
        self.log_panel.setMaximumHeight(160)
        layout.addWidget(self.log_panel)

        self.sources = QTableWidget(0, 2)
        self.sources.setHorizontalHeaderLabels(["字段", "来源"])
        self.sources.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.sources)

        self.pos_table = QTableWidget(0, 15)
        self.pos_table.setHorizontalHeaderLabels(
            ["代码", "名称", "类型", "数量", "成本", "最新价", "涨跌幅%", "MA50", "MA200", "ATR14", "回撤", "信号", "建议卖出", "建议买入", "备注"]
        )
        self.pos_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.pos_table, 2)

        btn_row = QHBoxLayout()
        self.mark_buy_btn = QPushButton("标记：已执行买入（更新冷却）")
        self.mark_buy_btn.clicked.connect(self.mark_buy_executed)
        btn_row.addWidget(self.mark_buy_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return w

    def _set_sources(self, sources: dict[str, str]) -> None:
        self.sources.setRowCount(0)
        for i, k in enumerate(sorted(sources.keys())):
            self.sources.insertRow(i)
            self.sources.setItem(i, 0, QTableWidgetItem(k))
            self.sources.setItem(i, 1, QTableWidgetItem(str(sources[k])))

    def _render_plan_text(self, plan) -> str:
        lines: list[str] = []
        lines.append("Quant-Terminal 战报")
        lines.append(f"日期：{plan.as_of.isoformat()}")
        lines.append("")
        lines.append("核心指标")
        lines.append(f"- ERP：{plan.erp:.4f}%")
        lines.append(f"- ERP 倍率：{plan.erp_multiplier:.2f}x")
        lines.append(f"- 本周滴灌释放：{_fmt_money(plan.drip_amount)}")
        lines.append("")
        lines.append("本周建议定投")
        lines.append(f"- 盾：{_fmt_money(plan.shield_amount)}")
        lines.append(f"- 矛：{_fmt_money(plan.spear_amount)}")
        if plan.notes:
            lines.append("")
            lines.append("备注")
            for n in plan.notes:
                lines.append(f"- {n}")
        return "\n".join(lines)

    def on_run_clicked(self) -> None:
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        try:
            as_of = self.date_edit.date().toPython()
            take_profit_amount = float(self.take_profit.value())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "输入错误", str(e))
            self.run_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        cfg = self.current_config()
        self.progress.setValue(0)
        self.log_panel.clear()
        self.status.setText("正在运行（后台线程）…")

        if self.force_fallback.isChecked():
            self._on_need_erp_fallback(FetchFailed.from_missing(metric="erp_inputs", missing_fields=["hs300_pe", "bond_yield_10y"]))
            return

        self._start_worker(as_of=as_of, take_profit_amount=take_profit_amount, cfg=cfg)

    def on_cancel_clicked(self) -> None:
        if self._run_worker is not None:
            self._run_worker.request_cancel()
        self.status.setText("正在取消…（已发出取消请求）")
        self.cancel_btn.setEnabled(False)
        # if underlying libs hang, offer a hard kill fallback
        self.force_stop_btn.setEnabled(True)

    def on_force_stop_clicked(self) -> None:
        if self._run_thread is not None and self._run_thread.isRunning():
            # Unsafe but practical for stuck network calls in 3rd-party libs.
            try:
                self._run_thread.terminate()
                self._run_thread.wait(1500)
            except Exception:
                pass
        self._cleanup_worker()
        self.status.setText("已强制停止。")
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.force_stop_btn.setEnabled(False)
        self.progress.setValue(0)
        self._append_log("已强制停止本次运行（可能存在未释放的后台请求）。")

    def _append_log(self, msg: str) -> None:
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        for line in str(msg).splitlines():
            self.log_panel.append(f"[{ts}] {line}")
        self.log_panel.ensureCursorVisible()

    def _cleanup_worker(self) -> None:
        if self._run_thread is not None:
            try:
                self._run_thread.quit()
                self._run_thread.wait(1500)
            except Exception:
                pass
        self._run_thread = None
        self._run_worker = None

    def _start_worker(self, *, as_of: date, take_profit_amount: float, cfg: AppConfig) -> None:
        # stop previous run if any
        self._cleanup_worker()

        params = RunParams(
            as_of=as_of,
            take_profit_amount=float(take_profit_amount),
            config=cfg,
            year_override=self._year_override,
            hs300_pe_override=self._hs300_pe_override,
            bond_yield_10y_override=self._bond_yield_10y_override,
            realtime_overrides=self._realtime_overrides,
        )

        self._run_thread = QThread(self)
        self._run_worker = RunWorker(params)
        self._run_worker.moveToThread(self._run_thread)

        self._run_thread.started.connect(self._run_worker.run)
        self._run_worker.log.connect(self._append_log)
        self._run_worker.progress.connect(self._on_progress)
        self._run_worker.plan_ready.connect(self._on_plan_ready)
        self._run_worker.sources_ready.connect(self._set_sources)
        self._run_worker.positions_ready.connect(self._on_positions_ready)
        self._run_worker.need_erp_fallback.connect(self._on_need_erp_fallback)
        self._run_worker.need_realtime_overrides.connect(self._on_need_realtime_overrides)
        self._run_worker.cancelled.connect(self._on_cancelled)
        self._run_worker.failed.connect(self._on_failed)
        self._run_worker.finished.connect(self._on_finished)
        self._run_worker.finished.connect(self._cleanup_worker)
        self._run_worker.cancelled.connect(self._cleanup_worker)
        self._run_worker.failed.connect(self._cleanup_worker)

        self._run_thread.start()

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if total <= 0:
            self.progress.setValue(0)
        else:
            pct = int(round((max(0, current) / float(total)) * 100.0))
            self.progress.setValue(min(100, max(0, pct)))
        if message:
            self.status.setText(message)

    def _on_plan_ready(self, plan) -> None:
        self.last_plan = plan
        self.report.setPlainText(self._render_plan_text(plan))
        self.refresh_history()

    def _on_positions_ready(self, rows) -> None:
        self._render_positions(rows)

    def _on_need_erp_fallback(self, e: FetchFailed) -> None:
        # worker stopped; prompt user then rerun with overrides
        self._cleanup_worker()
        missing = set(getattr(e, "missing_fields", []) or ["hs300_pe", "bond_yield_10y"])
        as_of = self.date_edit.date().toPython()
        dlg = ManualFallbackDialog(
            missing_fields=missing,
            default_year=as_of.year,
            default_hs300_pe=None,
            default_bond_yield_10y=None,
            parent=self,
        )
        if dlg.exec() != dlg.Accepted:
            self.status.setText("已取消。")
            self.run_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return
        res = dlg.result_values()
        self._year_override = res.year
        self._hs300_pe_override = res.hs300_pe
        self._bond_yield_10y_override = res.bond_yield_10y
        self._append_log("已补齐缺失 ERP 字段，继续运行…")
        self._start_worker(as_of=as_of, take_profit_amount=float(self.take_profit.value()), cfg=self.current_config())

    def _on_need_realtime_overrides(self, missing_list) -> None:
        self._cleanup_worker()
        # prompt user once for all missing, then rerun
        for m in list(missing_list or []):
            dlg = RealtimeOverrideDialog(code=m.code, kind=m.kind, name=m.name, reason=m.reason, parent=self)
            if dlg.exec() != dlg.Accepted:
                continue
            v = dlg.values()
            self._realtime_overrides[m.code] = (v.name or m.name, v.last_price, v.chg_pct)
        self._append_log("已补齐缺失实时行情，继续运行…")
        as_of = self.date_edit.date().toPython()
        self._start_worker(as_of=as_of, take_profit_amount=float(self.take_profit.value()), cfg=self.current_config())

    def _on_cancelled(self) -> None:
        self.status.setText("已取消。")
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.force_stop_btn.setEnabled(False)
        self.progress.setValue(0)
        self._append_log("运行已取消。")

    def _on_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "运行失败", msg)
        self.status.setText("失败。")
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.force_stop_btn.setEnabled(False)
        self._append_log(f"失败：{msg}")

    def _on_finished(self) -> None:
        self.status.setText("完成。")
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.force_stop_btn.setEnabled(False)
        self.progress.setValue(100)
        self._append_log("完成。")

    def _cooldown_codes(self, as_of: date, cooldown_days: int = 3) -> set[str]:
        out: set[str] = set()
        for r in self.storage.list_portfolio():
            iso = self.storage.get_last_buy_date(r["code"])
            if not iso:
                continue
            try:
                last = date.fromisoformat(str(iso))
            except Exception:
                continue
            if (as_of - last).days < cooldown_days:
                out.add(r["code"])
        return out

    def _render_positions(self, rows) -> None:
        self.pos_table.setRowCount(0)
        for i, r in enumerate(rows):
            self.pos_table.insertRow(i)
            self.pos_table.setItem(i, 0, QTableWidgetItem(r.code))
            self.pos_table.setItem(i, 1, QTableWidgetItem(r.name or ""))
            self.pos_table.setItem(i, 2, QTableWidgetItem(r.kind))
            self.pos_table.setItem(i, 3, QTableWidgetItem(str(r.qty)))
            self.pos_table.setItem(i, 4, QTableWidgetItem(str(r.cost)))
            self.pos_table.setItem(i, 5, QTableWidgetItem("" if r.last_price is None else str(r.last_price)))
            self.pos_table.setItem(i, 6, QTableWidgetItem("" if r.chg_pct is None else str(r.chg_pct)))
            self.pos_table.setItem(i, 7, QTableWidgetItem("" if r.ma50 is None else str(r.ma50)))
            self.pos_table.setItem(i, 8, QTableWidgetItem("" if r.ma200 is None else str(r.ma200)))
            self.pos_table.setItem(i, 9, QTableWidgetItem("" if r.atr14 is None else str(r.atr14)))
            self.pos_table.setItem(i, 10, QTableWidgetItem("" if r.drawdown is None else str(r.drawdown)))
            self.pos_table.setItem(i, 11, QTableWidgetItem(r.signal))
            suggestion = f"{r.action} {r.sell_qty}" if r.sell_qty else r.action
            self.pos_table.setItem(i, 12, QTableWidgetItem(suggestion))
            buy_suggestion = f"买入 {r.buy_qty}" if r.buy_qty else ("买入" if r.action == "买入" else "")
            self.pos_table.setItem(i, 13, QTableWidgetItem(buy_suggestion))
            self.pos_table.setItem(i, 14, QTableWidgetItem(r.notes or ""))

    def mark_buy_executed(self) -> None:
        items = self.pos_table.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先在表格中选中一个标的。")
            return
        row = items[0].row()
        code_item = self.pos_table.item(row, 0)
        buy_item = self.pos_table.item(row, 13)
        if code_item is None:
            return
        code = code_item.text().strip()
        buy_text = (buy_item.text().strip() if buy_item else "")
        if not buy_text:
            QMessageBox.information(self, "提示", "该标的当前没有买入建议。")
            return
        as_of = self.date_edit.date().toPython()
        self.storage.set_last_buy_date(code, as_of.isoformat())
        QMessageBox.information(self, "已更新", f"已记录 {code} 的买入执行日期为 {as_of.isoformat()}（冷却期生效）。")
        # rerun analysis (keeps overrides) in background
        self.on_run_clicked()

    # ---------------------------
    # History tab
    # ---------------------------
    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        row = QHBoxLayout()
        self.refresh_history_btn = QPushButton("刷新")
        self.refresh_history_btn.clicked.connect(self.refresh_history)
        row.addWidget(self.refresh_history_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.ledger_table = QTableWidget(0, 5)
        self.ledger_table.setHorizontalHeaderLabels(["ID", "时间", "ERP", "盾", "矛"])
        self.ledger_table.horizontalHeader().setStretchLastSection(True)
        self.ledger_table.itemSelectionChanged.connect(self.on_history_selected)
        layout.addWidget(self.ledger_table, 1)

        self.ledger_detail = QTextEdit()
        self.ledger_detail.setReadOnly(True)
        self.ledger_detail.setPlaceholderText("选择一条记录查看详细内容。")
        layout.addWidget(self.ledger_detail, 1)

        return w

    def refresh_history(self) -> None:
        rows = self.storage.list_ledger(limit=50)
        self.ledger_cache = rows
        self.ledger_table.setRowCount(0)
        for i, r in enumerate(rows):
            payload = r.get("payload", {})
            plan = payload.get("plan", {})
            self.ledger_table.insertRow(i)
            self.ledger_table.setItem(i, 0, QTableWidgetItem(str(r.get("id"))))
            self.ledger_table.setItem(i, 1, QTableWidgetItem(str(r.get("ts"))))
            self.ledger_table.setItem(i, 2, QTableWidgetItem(str(plan.get("erp", ""))))
            self.ledger_table.setItem(i, 3, QTableWidgetItem(str(plan.get("shield_amount", ""))))
            self.ledger_table.setItem(i, 4, QTableWidgetItem(str(plan.get("spear_amount", ""))))

    def on_history_selected(self) -> None:
        items = self.ledger_table.selectedItems()
        if not items:
            return
        row_idx = items[0].row()
        if row_idx < 0 or row_idx >= len(self.ledger_cache):
            return
        payload = self.ledger_cache[row_idx].get("payload", {})
        self.ledger_detail.setPlainText(str(payload))

    # ---------------------------
    # Settings tab
    # ---------------------------
    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        info = QLabel("设置会保存到 SQLite，并在下次运行时生效。")
        info.setWordWrap(True)
        layout.addWidget(info)

        form_box = QGroupBox("核心参数")
        form = QFormLayout(form_box)
        self.settings_inputs: dict[str, QWidget] = {}
        for key, label, typ in SETTINGS_FIELDS:
            if typ == "int":
                inp = QSpinBox()
                inp.setRange(0, 10_000_000)
            else:
                inp = QDoubleSpinBox()
                inp.setRange(-1e12, 1e12)
                inp.setDecimals(6)
            self.settings_inputs[key] = inp
            form.addRow(label, inp)

        layout.addWidget(form_box)

        row = QHBoxLayout()
        self.settings_save_btn = QPushButton("保存")
        self.settings_save_btn.clicked.connect(self.save_settings_form)
        row.addWidget(self.settings_save_btn)
        self.settings_reset_btn = QPushButton("恢复默认")
        self.settings_reset_btn.clicked.connect(self.reset_settings_form)
        row.addWidget(self.settings_reset_btn)
        row.addStretch(1)
        layout.addLayout(row)

        return w

    def refresh_settings_form(self) -> None:
        cfg = self.current_config()
        for key, _label, typ in SETTINGS_FIELDS:
            w = self.settings_inputs[key]
            val = getattr(cfg, key)
            if typ == "int":
                w.setValue(int(val))  # type: ignore[attr-defined]
            else:
                w.setValue(float(val))  # type: ignore[attr-defined]

    def save_settings_form(self) -> None:
        for key, _label, typ in SETTINGS_FIELDS:
            w = self.settings_inputs[key]
            if typ == "int":
                val = int(w.value())  # type: ignore[attr-defined]
            else:
                val = float(w.value())  # type: ignore[attr-defined]
            self.storage.set_setting(key, str(val))
        QMessageBox.information(self, "已保存", "设置已保存。")

    def reset_settings_form(self) -> None:
        # Clear settings by overwriting with empty -> simplest: set to defaults explicitly
        cfg = DEFAULT_CONFIG
        for key, _label, typ in SETTINGS_FIELDS:
            self.storage.set_setting(key, str(getattr(cfg, key)))
        self.refresh_settings_form()
        QMessageBox.information(self, "已恢复默认", "已恢复为默认设置。")

    # ---------------------------
    # Watchlist tab
    # ---------------------------
    def _build_watchlist_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        row = QHBoxLayout()
        self.wl_code = QLineEdit()
        self.wl_code.setPlaceholderText("代码（例如 510300 / 110011）")
        row.addWidget(self.wl_code)
        self.wl_kind = QComboBox()
        self.wl_kind.addItems(["fund", "etf", "stock"])
        row.addWidget(self.wl_kind)
        self.wl_alias = QLineEdit()
        self.wl_alias.setPlaceholderText("备注名（可选）")
        row.addWidget(self.wl_alias)
        self.wl_add = QPushButton("添加/更新")
        self.wl_add.clicked.connect(self.on_watchlist_add)
        row.addWidget(self.wl_add)
        self.wl_del = QPushButton("删除")
        self.wl_del.clicked.connect(self.on_watchlist_delete)
        row.addWidget(self.wl_del)
        layout.addLayout(row)

        self.wl_table = QTableWidget(0, 4)
        self.wl_table.setHorizontalHeaderLabels(["代码", "类型", "备注名", "启用"])
        self.wl_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.wl_table, 1)

        return w

    def refresh_watchlist(self) -> None:
        rows = self.storage.list_watchlist()
        self.wl_table.setRowCount(0)
        for i, r in enumerate(rows):
            self.wl_table.insertRow(i)
            self.wl_table.setItem(i, 0, QTableWidgetItem(str(r["code"])))
            self.wl_table.setItem(i, 1, QTableWidgetItem(str(r["kind"])))
            self.wl_table.setItem(i, 2, QTableWidgetItem(str(r.get("alias") or "")))
            self.wl_table.setItem(i, 3, QTableWidgetItem("1" if r.get("enabled", 1) else "0"))

    def on_watchlist_add(self) -> None:
        code = self.wl_code.text().strip()
        if not code:
            return
        kind = self.wl_kind.currentText()
        alias = self.wl_alias.text().strip() or None
        self.storage.upsert_watchlist(code=code, kind=kind, alias=alias, enabled=True)
        self.refresh_watchlist()

    def on_watchlist_delete(self) -> None:
        code = self.wl_code.text().strip()
        if not code:
            items = self.wl_table.selectedItems()
            if items:
                code = items[0].text()
        if not code:
            return
        self.storage.delete_watchlist(code)
        self.refresh_watchlist()

    # ---------------------------
    # Portfolio tab
    # ---------------------------
    def _build_portfolio_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        row = QHBoxLayout()
        self.pf_code = QLineEdit()
        self.pf_code.setPlaceholderText("代码")
        row.addWidget(self.pf_code)
        self.pf_kind = QComboBox()
        self.pf_kind.addItems(["fund", "etf", "stock"])
        row.addWidget(self.pf_kind)
        self.pf_qty = QDoubleSpinBox()
        self.pf_qty.setRange(0.0, 1e12)
        self.pf_qty.setDecimals(6)
        row.addWidget(self.pf_qty)
        self.pf_cost = QDoubleSpinBox()
        self.pf_cost.setRange(0.0, 1e12)
        self.pf_cost.setDecimals(6)
        row.addWidget(self.pf_cost)
        self.pf_add = QPushButton("添加/更新")
        self.pf_add.clicked.connect(self.on_portfolio_add)
        row.addWidget(self.pf_add)
        self.pf_del = QPushButton("删除")
        self.pf_del.clicked.connect(self.on_portfolio_delete)
        row.addWidget(self.pf_del)
        layout.addLayout(row)

        self.pf_table = QTableWidget(0, 4)
        self.pf_table.setHorizontalHeaderLabels(["代码", "类型", "数量", "成本"])
        self.pf_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.pf_table, 1)

        return w

    def refresh_portfolio(self) -> None:
        rows = self.storage.list_portfolio()
        self.pf_table.setRowCount(0)
        for i, r in enumerate(rows):
            self.pf_table.insertRow(i)
            self.pf_table.setItem(i, 0, QTableWidgetItem(str(r["code"])))
            self.pf_table.setItem(i, 1, QTableWidgetItem(str(r["kind"])))
            self.pf_table.setItem(i, 2, QTableWidgetItem(str(r["qty"])))
            self.pf_table.setItem(i, 3, QTableWidgetItem(str(r["cost"])))

    def on_portfolio_add(self) -> None:
        code = self.pf_code.text().strip()
        if not code:
            return
        kind = self.pf_kind.currentText()
        qty = float(self.pf_qty.value())
        cost = float(self.pf_cost.value())
        self.storage.upsert_portfolio(code=code, kind=kind, qty=qty, cost=cost)
        self.refresh_portfolio()

    def on_portfolio_delete(self) -> None:
        code = self.pf_code.text().strip()
        if not code:
            items = self.pf_table.selectedItems()
            if items:
                code = items[0].text()
        if not code:
            return
        self.storage.delete_portfolio(code)
        self.refresh_portfolio()


def main() -> None:
    _configure_frozen_runtime()
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

