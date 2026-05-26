"""
Configuration — all values from environment with sensible defaults.
"""

import os
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent

# ── API ───────────────────────────────────────────────────────────────────────
API_URL = os.environ.get(
    "PILOTAI_API_URL",
    "https://ai-stag.pilotai.com/v2/strategy_recommendation",
)
API_KEY = os.environ.get("PILOTAI_API_KEY", "cZZP6he1Qez8Lb6njh6w5vUe")
API_BATCH_SIZE = int(os.environ.get("PILOTAI_BATCH_SIZE", "6"))
API_TIMEOUT = int(os.environ.get("PILOTAI_REQUEST_TIMEOUT", "90"))

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = Path(
    os.environ.get("PILOTAI_DB_PATH", str(PROJECT_ROOT / "data" / "pilotai_signal.db"))
)

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Scoring weights ───────────────────────────────────────────────────────────
SCORE_WEIGHT_FREQ = 0.40
SCORE_WEIGHT_PERSISTENCE = 0.35
SCORE_WEIGHT_QUALITY = 0.25
PERSISTENCE_CAP_DAYS = 30  # persistence saturates after 30 days

# QScore weights (applied to Attix stock_score 0-5 dimensions)
QSCORE_GROWTH = 0.35
QSCORE_MOMENTUM = 0.25
QSCORE_VALUE = 0.20
QSCORE_HEALTH = 0.15
QSCORE_PAST = 0.05

# ── Alert thresholds ──────────────────────────────────────────────────────────
ALERT_STRONG_CONVICTION_MIN = 0.70      # conviction >= this → STRONG candidate
ALERT_STRONG_CONSECUTIVE_DAYS = 3       # must hold for this many days
ALERT_MOVER_DELTA = 0.15                # |Δconviction| >= this → MOVER
ALERT_NEW_ABSENCE_DAYS = 5             # re-fires NEW if ticker absent this many days
ALERT_DIGEST_TOP_N = 10                 # tickers in daily digest

# ── Alert filtering / noise suppression ───────────────────────────────────────
# NEW alerts: only send when conviction clears this bar (suppress weaker entries)
ALERT_MIN_CONVICTION_NEW = 0.70
# MOVER_UP alerts: resulting conviction must reach this level
ALERT_MIN_CONVICTION_MOVER = 0.60
# MOVER_UP alerts: delta must be at least this large (mirrors ALERT_MOVER_DELTA)
ALERT_MIN_DELTA_MOVER = ALERT_MOVER_DELTA
# Alert types suppressed from Telegram (informational / watchlist only)
ALERT_SUPPRESS_TYPES = ["STRONG", "MOVER_DOWN"]
# Max one MOVER_UP alert per ticker within this many days
ALERT_MOVER_COOLDOWN_DAYS = 7
# JSON file tracking last-fired date per ticker for MOVER_UP cooldown
MOVER_COOLDOWN_FILE = PROJECT_ROOT / "data" / "mover_up_cooldown.json"

# Gold / precious metals tickers (flagged separately in digest)
GOLD_TICKERS = {
    "GLD", "IAU", "SGOL", "GLDM", "GDX", "GDXJ",
    "NEM", "AEM", "AU", "RGLD", "WPM", "GOLD",
    "FNV", "AUY", "AGI", "KGC", "BTG", "DRD", "OR",
    "OUNZ", "RING", "SGDM", "GOEX", "DBP", "SSRM",
    "FCX", "NEXA", "LXU",
}

# ── All 57 strategy slugs (canonical list) ────────────────────────────────────
ALL_SLUGS = [
    "5g-infrastructure", "aging-population", "ai-related-companies",
    "biomedical-and-genetics-industry", "biotech-breakthroughs", "buffett-bargains",
    "clean-energy-revolution", "cloud-computing-boom", "consumer-discretionary",
    "consumer-staples-stability-strategy", "contrarian-investing", "cybersecurity-shield",
    "deep-value-investing", "defensive-investing", "diversified-bluechips",
    "dividend-aristocrats", "drip-dividend-reinvestment-plan", "e-commerce-enablers",
    "electric-vehicle-ev-boom", "energy-sector-growth-strategy", "esg-leaders",
    "fallen-angels", "financials-sector-capital-strategy", "gaming-giants",
    "global-investing", "gold-mining-industry", "green-infrastructure",
    "growth-investing", "healthcare-sector-stability-and-growth-fund",
    "high-beta-stocks", "high-dividend-stocks",
    "industrials-sector-infrastructure-fund", "investment-management-industry",
    "leisure-and-recreation-services-industry", "low-beta-stocks",
    "low-volatility-stocks", "manufacturing-industry", "market-disruptors",
    "meme-stock-mania", "metaverse-pioneers", "mid-cap-stocks",
    "momentum-investing", "quality-investing", "real-estate-sector-income-fund",
    "robotics-automation", "sector-rotation", "semiconductor-supercycle",
    "small-cap-stocks", "socially-responsible-investing-sri", "space-exploration",
    "technology-sector-innovation-fund", "the-amazon-of-x", "thematic-investing",
    "transportation-airline-sector", "utilities-sector-stability-fund",
    "value-investing", "water-scarcity-solutions",
]
