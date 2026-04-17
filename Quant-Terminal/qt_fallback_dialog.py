from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


@dataclass(frozen=True)
class ManualFallbackResult:
    year: int | None
    hs300_pe: float | None
    bond_yield_10y: float | None


class ManualFallbackDialog(QDialog):
    def __init__(
        self,
        *,
        missing_fields: set[str],
        default_year: int,
        default_hs300_pe: float | None,
        default_bond_yield_10y: float | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("手工回退输入")
        self.setModal(True)

        self._missing_fields = set(missing_fields)

        root = QVBoxLayout(self)

        info = QLabel(
            "线上抓取失败，部分字段缺失。\n"
            "请补齐缺失的数值，然后点击「确定」继续计算。"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        form = QFormLayout()
        root.addLayout(form)

        self.year_edit = QLineEdit(str(default_year))
        self.year_edit.setValidator(QIntValidator(2000, 3000, self))
        form.addRow("年份", self.year_edit)

        self.pe_edit = QLineEdit("" if default_hs300_pe is None else str(default_hs300_pe))
        self.pe_edit.setValidator(QDoubleValidator(0.0001, 9999999.0, 6, self))
        self.pe_edit.setPlaceholderText("例如 12.8")
        form.addRow("沪深300 动态PE", self.pe_edit)

        self.bond_edit = QLineEdit("" if default_bond_yield_10y is None else str(default_bond_yield_10y))
        self.bond_edit.setValidator(QDoubleValidator(0.0, 50.0, 6, self))
        self.bond_edit.setPlaceholderText("例如 2.35 或 2.35%")
        form.addRow("10年期国债收益率(%)", self.bond_edit)

        # Disable fields that are not missing (still editable if you want to override year)
        if "hs300_pe" not in self._missing_fields:
            self.pe_edit.setEnabled(False)
            self.pe_edit.setToolTip("已成功抓取，无需输入。")
        if "bond_yield_10y" not in self._missing_fields:
            self.bond_edit.setEnabled(False)
            self.bond_edit.setToolTip("已成功抓取，无需输入。")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_values(self) -> ManualFallbackResult:
        year = int(self.year_edit.text()) if self.year_edit.text().strip() else None

        hs300_pe = None
        if self.pe_edit.isEnabled():
            t = self.pe_edit.text().strip()
            hs300_pe = float(t) if t else None

        bond = None
        if self.bond_edit.isEnabled():
            t = self.bond_edit.text().strip().rstrip("%").strip()
            bond = float(t) if t else None

        return ManualFallbackResult(year=year, hs300_pe=hs300_pe, bond_yield_10y=bond)

