from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout


@dataclass(frozen=True)
class RealtimeOverride:
    name: str | None
    last_price: float | None
    chg_pct: float | None


class RealtimeOverrideDialog(QDialog):
    def __init__(self, *, code: str, kind: str, name: str | None, reason: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("补充实时行情")
        self.setModal(True)

        root = QVBoxLayout(self)
        info = QLabel(f"标的：{code}（{kind}）\n{name or ''}\n原因：{reason}\n\n请输入实时行情以继续：")
        info.setWordWrap(True)
        root.addWidget(info)

        form = QFormLayout()
        root.addLayout(form)

        self.name_edit = QLineEdit(name or "")
        form.addRow("名称（可选）", self.name_edit)

        self.price_edit = QLineEdit("")
        self.price_edit.setValidator(QDoubleValidator(0.0, 1e12, 6, self))
        self.price_edit.setPlaceholderText("例如 1467.45")
        form.addRow("最新价", self.price_edit)

        self.chg_edit = QLineEdit("")
        self.chg_edit.setValidator(QDoubleValidator(-100.0, 1000.0, 6, self))
        self.chg_edit.setPlaceholderText("例如 -0.32 或 -0.32%")
        form.addRow("涨跌幅(%)", self.chg_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> RealtimeOverride:
        name = self.name_edit.text().strip() or None

        price = self.price_edit.text().strip()
        last_price = float(price) if price else None

        chg = self.chg_edit.text().strip().rstrip("%").strip()
        chg_pct = float(chg) if chg else None

        return RealtimeOverride(name=name, last_price=last_price, chg_pct=chg_pct)

