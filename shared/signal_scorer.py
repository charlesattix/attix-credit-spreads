"""
Port of the 5-component scoring from strategy/spread_strategy.py
to work on Signal objects from the new strategy system.

Scoring components (0-100 total):
  1. Credit quality (0-25): credit as % of spread width
  2. Risk/reward (0-25): favorable R:R ratio
  3. Probability of profit (0-25): estimated POP
  4. Technical alignment (0-15): trend + regime match
  5. IV rank (0-10): elevated IV preferred
"""

from typing import Dict, Optional

from strategies.base import Signal


# Scoring weights — match strategy/spread_strategy.py SCORING_WEIGHTS
SCORING_WEIGHTS = {
    "credit_max": 25,
    "credit_scale": 0.5,
    "risk_reward_max": 25,
    "risk_reward_scale": 8,
    "pop_max": 25,
    "pop_baseline": 85,
    "technical_max": 15,
    "tech_strong_signal": 10,
    "tech_neutral_signal": 5,
    "tech_support_resistance": 5,
    "iv_max": 10,
    "iv_divisor": 10,
    "condor_tech_neutral": 10,
    "condor_tech_regime": 5,
    "condor_tech_rsi_range": 5,
}


def score_signal(
    signal: Signal,
    iv_rank: float = 25.0,
    technical_signals: Optional[Dict] = None,
) -> float:
    """Score a Signal using the 5-component scoring system.

    Args:
        signal: Signal object from strategy.generate_signals().
        iv_rank: Current IV rank (0-100).
        technical_signals: Dict with 'trend', 'rsi', 'near_support', 'near_resistance'.

    Returns:
        Score 0-100.
    """
    w = SCORING_WEIGHTS
    tech = technical_signals or {}
    score = 0.0

    spread_type = signal.metadata.get("spread_type", "")
    is_condor = "condor" in signal.strategy_name.lower() or "condor" in spread_type
    is_straddle = "straddle" in spread_type or "strangle" in spread_type

    # 1. Credit quality — credit as % of spread width
    spread_width = 0.0
    if len(signal.legs) >= 2:
        spread_width = abs(signal.legs[0].strike - signal.legs[1].strike)
    if spread_width > 0 and signal.net_credit > 0:
        credit_pct = (signal.net_credit / spread_width) * 100
        score += min(credit_pct * w["credit_scale"], w["credit_max"])
    elif is_straddle and signal.net_credit > 0:
        # Straddles: use credit/max_loss ratio instead
        if signal.max_loss > 0:
            credit_ratio = signal.net_credit / signal.max_loss * 100
            score += min(credit_ratio * w["credit_scale"], w["credit_max"])

    # 2. Risk/reward
    risk_reward = 0.0
    if signal.max_loss > 0:
        risk_reward = signal.max_profit / signal.max_loss
    rr_score = min(risk_reward * w["risk_reward_scale"], w["risk_reward_max"])
    score += rr_score

    # 3. POP estimate — from metadata or heuristic
    pop = signal.metadata.get("pop", 0)
    if pop == 0 and signal.net_credit > 0 and spread_width > 0:
        # Heuristic: POP ~ 1 - (credit / spread_width) for credit spreads
        pop = (1 - signal.net_credit / spread_width) * 100
    if pop > 0:
        pop_score = min((pop / w["pop_baseline"]) * w["pop_max"], w["pop_max"])
        score += pop_score

    # 4. Technical alignment
    tech_score = 0.0
    if is_condor:
        if tech.get("trend") == "neutral":
            tech_score += w["condor_tech_neutral"]
        rsi = tech.get("rsi", signal.metadata.get("rsi"))
        if rsi is not None and 40 <= rsi <= 60:
            tech_score += w["condor_tech_rsi_range"]
    elif is_straddle:
        # Straddles benefit from high uncertainty (neutral trend)
        tech_score += w["tech_neutral_signal"]
    elif spread_type == "bull_put":
        if tech.get("trend") == "bullish":
            tech_score += w["tech_strong_signal"]
        elif tech.get("trend") in ("neutral", None):
            tech_score += w["tech_neutral_signal"]
    elif spread_type == "bear_call":
        if tech.get("trend") == "bearish":
            tech_score += w["tech_strong_signal"]
        elif tech.get("trend") in ("neutral", None):
            tech_score += w["tech_neutral_signal"]
    else:
        # Default: neutral alignment
        tech_score += w["tech_neutral_signal"]

    score += min(tech_score, w["technical_max"])

    # 5. IV rank
    iv_score = min(iv_rank / w["iv_divisor"], w["iv_max"])
    score += iv_score

    return round(score, 2)
