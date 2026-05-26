// Canonical domain types for the Attix credit spread system

export type TradeStatus = 'open' | 'closed_profit' | 'closed_loss' | 'closed_expiry' | 'closed_manual';

export interface PaperTrade {
  id: string;
  ticker: string;
  type: string; // e.g. "bear_call_spread", "bull_put_spread"
  short_strike: number;
  long_strike: number;
  spread_width: number;
  expiration: string;
  dte_at_entry: number;
  entry_credit: number;
  entry_price: number;
  current_price?: number;
  contracts: number;
  max_profit: number;
  max_loss: number;
  status: TradeStatus;
  entry_date: string;
  exit_date?: string;
  exit_credit?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  days_remaining?: number;
  profit_target?: number;
  stop_loss?: number;
  pop?: number;
  score?: number;
  short_delta?: number;
  alpaca_order_id?: string;
  alpaca_status?: string;
  alpaca_filled_price?: number;
}

export interface Portfolio {
  trades: PaperTrade[];
  starting_balance: number;
  created_at: string;
  user_id: string;
}

export interface Alert {
  ticker: string
  type: string
  expiration: string
  dte: number
  short_strike: number
  long_strike: number
  short_delta: number
  credit: number
  max_loss: number
  max_profit: number
  profit_target: number
  stop_loss: number
  spread_width: number
  current_price: number
  distance_to_short: number
  pop: number
  risk_reward: number
  score: number
  // Iron condor fields
  call_short_strike?: number
  call_long_strike?: number
  put_credit?: number
  call_credit?: number
}

export interface BacktestResult {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  equity_curve: Array<{ date: string; equity: number }>;
  trade_distribution: Array<{ range: string; count: number }>;
}

export interface Position {
  ticker: string;
  type: string;
  short_strike: number;
  long_strike: number;
  unrealized_pnl: number;
  credit?: number;
  entry_date?: string;
  dte?: number;
  current_price?: number;
  profit_target?: number;
  stop_loss?: number;
  contracts?: number;
  total_credit?: number;
  total_max_loss?: number;
  days_remaining?: number;
  days_held?: number;
  max_profit?: number;
  pnl_pct?: number;
  expiration?: string;
  alpaca_status?: string;
}

export interface PortfolioStats {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  winners: number;
  losers: number;
  win_rate: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number;
  total_pnl: number;
  balance: number;
  starting_balance: number;
}

export interface PositionsSummary {
  account_size: number;
  starting_balance: number;
  current_balance: number;
  total_pnl: number;
  total_realized_pnl: number;
  total_unrealized_pnl: number;
  total_trades: number;
  open_count: number;
  closed_count: number;
  win_rate: number;
  total_credit: number;
  total_max_loss: number;
  open_positions: PaperTrade[];
  closed_trades: PaperTrade[];
}

export interface AlertsResponse {
  timestamp: string
  alerts: Alert[]
  opportunities: Alert[]
  count: number
}

export interface Trade {
  id: string
  ticker: string
  type: string
  entry_date: string
  exit_date?: string
  short_strike: number
  long_strike: number
  credit: number
  debit?: number
  pnl?: number
  status: 'open' | 'closed'
  entry_price: number
  exit_price?: number
  dte_entry: number
  dte_exit?: number
}

// ---------------------------------------------------------------------------
// Interactive Backtest Runner types
// ---------------------------------------------------------------------------

export interface BacktestTradeRecord {
  ticker: string;
  type: string;  // 'bull_put_spread' | 'bear_call_spread'
  entry_date: string;
  exit_date: string;
  exit_reason: string;
  short_strike: number;
  long_strike: number;
  credit: number;
  contracts: number;
  pnl: number;
  return_pct: number;
}

export interface BacktestRunResult {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown: number;
  starting_capital: number;
  ending_capital: number;
  return_pct: number;
  bull_put_trades: number;
  bear_call_trades: number;
  bull_put_win_rate: number;
  bear_call_win_rate: number;
  trades: BacktestTradeRecord[];
  equity_curve: Array<{ date: string; equity: number }>;
}

export interface BacktestJobStatus {
  jobId: string;
  status: 'running' | 'completed' | 'failed';
  result?: BacktestRunResult;
  error?: string;
}

export interface Config {
  tickers: string[]
  strategy: {
    min_dte: number
    max_dte: number
    manage_dte: number
    min_delta: number
    max_delta: number
    spread_width: number
    min_iv_rank: number
    min_iv_percentile: number
    technical: {
      use_trend_filter: boolean
      use_rsi_filter: boolean
      use_support_resistance: boolean
      fast_ma: number
      slow_ma: number
      rsi_period: number
      rsi_oversold: number
      rsi_overbought: number
    }
  }
  risk: {
    account_size: number
    max_risk_per_trade: number
    max_positions: number
    profit_target: number
    stop_loss_multiplier: number
    delta_threshold: number
    min_credit_pct: number
  }
  alerts: {
    output_json: boolean
    output_text: boolean
    output_csv: boolean
    json_file: string
    text_file: string
    csv_file: string
    telegram: {
      enabled: boolean
      bot_token: string
      chat_id: string
    }
  }
  data: {
    provider: string
    backtest_lookback: number
    use_cache: boolean
    cache_expiry_minutes: number
  }
  logging: {
    level: string
    file: string
    console: boolean
  }
  backtest: {
    starting_capital: number
    commission_per_contract: number
    slippage: number
    generate_reports: boolean
    report_dir: string
  }
}
