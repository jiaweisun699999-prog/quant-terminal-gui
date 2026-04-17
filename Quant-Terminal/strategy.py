from __future__ import annotations

from dataclasses import dataclass
from math import pow

from config import AppConfig
from errors import ValidationError
from models import DripState, ErpInputs, ErpResult, WeeklyBasePlan


@dataclass(frozen=True)
class TimeInflationPrincipal:
    """
    Inflate weekly principal bases by compounding annually from a base year.

    Example:
      base_year=2026, growth=0.15
      year=2027 => multiplier = 1.15
      year=2028 => multiplier = 1.15^2
    """

    config: AppConfig

    def weekly_base_for_year(self, year: int) -> WeeklyBasePlan:
        if year < 1900 or year > 3000:
            raise ValidationError(f"year out of range: {year}")
        delta_years = year - self.config.base_year
        multiplier = pow(1.0 + self.config.annual_principal_growth_rate, max(delta_years, 0))

        shield = round(self.config.shield_weekly_base * multiplier, 2)
        spear = round(self.config.spear_weekly_base * multiplier, 2)
        return WeeklyBasePlan(year=year, shield_base=shield, spear_base=spear)


@dataclass(frozen=True)
class ErpEngine:
    config: AppConfig

    def compute(self, inputs: ErpInputs) -> ErpResult:
        if inputs.hs300_pe <= 0:
            raise ValidationError("hs300_pe must be > 0")

        erp = (1.0 / inputs.hs300_pe) * 100.0 - inputs.bond_yield_10y

        if erp > self.config.erp_greed_threshold:
            mult = self.config.erp_greed_multiplier
        elif erp < self.config.erp_fear_threshold:
            mult = self.config.erp_fear_multiplier
        else:
            mult = self.config.erp_neutral_multiplier

        return ErpResult(erp=round(erp, 4), multiplier=mult)


@dataclass(frozen=True)
class ProfitDripSystem:
    """
    Maintain a 'drip queue' that releases a portion each week.

    Rule:
      - Any realized take-profit amount is not withdrawn.
      - It is evenly spread across the next N weeks (default N=12).
      - Each week you "release" queue[0], shift the queue, and append 0.

    Implementation detail:
      - When adding profit P, we add P/N to each slot in the queue to make it
        behave like an overlapping 12-week annuity even if multiple profits happen.
    """

    config: AppConfig

    def add_take_profit(self, state: DripState, take_profit_amount: float) -> DripState:
        if take_profit_amount < 0:
            raise ValidationError("take_profit_amount must be >= 0")
        if take_profit_amount == 0:
            return DripState(weeks=state.weeks, queue=list(state.queue))

        portion = take_profit_amount / float(state.weeks)
        new_queue = [round(x + portion, 10) for x in state.queue]
        return DripState(weeks=state.weeks, queue=new_queue)

    def release_this_week(self, state: DripState) -> tuple[float, DripState]:
        amount = float(state.queue[0])
        shifted = state.queue[1:] + [0.0]
        return round(amount, 2), DripState(weeks=state.weeks, queue=shifted)

