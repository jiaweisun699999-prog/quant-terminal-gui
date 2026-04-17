from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    # --- Base year & principal inflation ---
    base_year: int = 2026
    annual_principal_growth_rate: float = 0.15  # 15% per year

    # Weekly base amounts in base_year
    shield_weekly_base: float = 875.0
    spear_weekly_base: float = 375.0

    # --- ERP thresholds (percentage points) ---
    erp_greed_threshold: float = 5.5
    erp_fear_threshold: float = 3.0
    erp_greed_multiplier: float = 1.5
    erp_fear_multiplier: float = 0.5
    erp_neutral_multiplier: float = 1.0

    # --- Profit drip ---
    drip_weeks: int = 12

    # --- Storage ---
    sqlite_path: str = "quant_terminal.db"

    # --- Data fetch retry ---
    fetch_retries: int = 3

    # Qt 运行「整段 build_weekly_plan」的墙钟软超时（秒）；含多次重试与较慢网络下的两次 AkShare 调用
    erp_build_timeout_s: float = 60.0

    # --- Versioning (for ledger reproducibility) ---
    app_version: str = "0.2"
    strategy_version: str = "0.2"


DEFAULT_CONFIG = AppConfig()

