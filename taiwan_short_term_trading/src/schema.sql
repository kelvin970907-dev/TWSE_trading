CREATE TABLE IF NOT EXISTS trading_calendar (
    trade_date DATE PRIMARY KEY,
    is_open BOOLEAN,
    market TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS daily_prices (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    market TEXT,
    name TEXT,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume_shares BIGINT,
    turnover_twd DOUBLE,
    trades BIGINT,
    prev_close DOUBLE,
    daily_return DOUBLE,
    limit_up_price DOUBLE,
    limit_down_price DOUBLE,
    touched_limit_up BOOLEAN,
    touched_limit_down BOOLEAN,
    closed_limit_up BOOLEAN,
    closed_limit_down BOOLEAN,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS intraday_bars (
    symbol TEXT NOT NULL,
    trade_date DATE,
    market TEXT,
    bar_time TIMESTAMP NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume_shares BIGINT,
    turnover_twd DOUBLE,
    source TEXT,
    PRIMARY KEY (symbol, bar_time)
);

CREATE TABLE IF NOT EXISTS index_daily_prices (
    index_symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    turnover_twd DOUBLE,
    daily_return DOUBLE,
    ma5 DOUBLE,
    ma20 DOUBLE,
    ma60 DOUBLE,
    close_above_ma20 BOOLEAN,
    close_above_ma60 BOOLEAN,
    drawdown_from_60d_high DOUBLE,
    source TEXT,
    PRIMARY KEY (index_symbol, trade_date)
);

ALTER TABLE index_daily_prices ADD COLUMN IF NOT EXISTS drawdown_from_60d_high DOUBLE;
ALTER TABLE index_daily_prices ADD COLUMN IF NOT EXISTS source TEXT;

CREATE TABLE IF NOT EXISTS stock_sector_map (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    industry TEXT,
    source TEXT,
    PRIMARY KEY (symbol, market)
);

ALTER TABLE stock_sector_map ADD COLUMN IF NOT EXISTS name TEXT;

CREATE TABLE IF NOT EXISTS sector_daily_features (
    sector TEXT NOT NULL,
    trade_date DATE NOT NULL,
    equal_weight_return DOUBLE,
    value_weight_return DOUBLE,
    num_advancers BIGINT,
    num_decliners BIGINT,
    num_limit_up BIGINT,
    sector_momentum_5d DOUBLE,
    sector_momentum_20d DOUBLE,
    PRIMARY KEY (sector, trade_date)
);

CREATE TABLE IF NOT EXISTS institutional_flows (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    market TEXT,
    foreign_net_buy_twd DOUBLE,
    investment_trust_net_buy_twd DOUBLE,
    dealer_net_buy_twd DOUBLE,
    total_institutional_net_buy_twd DOUBLE,
    source TEXT,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS margin_short (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    market TEXT,
    margin_buy_balance BIGINT,
    margin_sell_balance BIGINT,
    short_sale_balance BIGINT,
    day_trade_volume BIGINT,
    source TEXT,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS event_candidates (
    event_id TEXT PRIMARY KEY,
    symbol TEXT,
    trade_date DATE,
    market TEXT,
    event_type TEXT,
    day0_return DOUBLE,
    day0_open DOUBLE,
    day0_high DOUBLE,
    day0_low DOUBLE,
    day0_close DOUBLE,
    day0_volume_shares BIGINT,
    day0_turnover_twd DOUBLE,
    close_location DOUBLE,
    volume_ratio_20d DOUBLE,
    touched_limit_up BOOLEAN,
    closed_limit_up BOOLEAN,
    failed_limit_up BOOLEAN,
    next_trade_date DATE,
    foreign_net_buy_twd DOUBLE,
    investment_trust_net_buy_twd DOUBLE,
    dealer_net_buy_twd DOUBLE,
    total_institutional_net_buy_twd DOUBLE,
    foreign_net_buy_to_turnover DOUBLE,
    investment_trust_net_buy_to_turnover DOUBLE,
    dealer_net_buy_to_turnover DOUBLE,
    total_institutional_net_buy_to_turnover DOUBLE,
    margin_buy_balance BIGINT,
    margin_sell_balance BIGINT,
    short_sale_balance BIGINT,
    day_trade_volume BIGINT,
    margin_balance_change_1d BIGINT,
    short_balance_change_1d BIGINT,
    short_squeeze_proxy DOUBLE,
    margin_crowding_proxy DOUBLE
);

ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS foreign_net_buy_twd DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS investment_trust_net_buy_twd DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS dealer_net_buy_twd DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS total_institutional_net_buy_twd DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS foreign_net_buy_to_turnover DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS investment_trust_net_buy_to_turnover DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS dealer_net_buy_to_turnover DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS total_institutional_net_buy_to_turnover DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS margin_buy_balance BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS margin_sell_balance BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS short_sale_balance BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS day_trade_volume BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS margin_balance_change_1d BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS short_balance_change_1d BIGINT;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS short_squeeze_proxy DOUBLE;
ALTER TABLE event_candidates ADD COLUMN IF NOT EXISTS margin_crowding_proxy DOUBLE;

CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id TEXT PRIMARY KEY,
    strategy_name TEXT,
    symbol TEXT,
    market TEXT,
    signal_date DATE,
    entry_date DATE,
    entry_time TIMESTAMP,
    exit_date DATE,
    exit_time TIMESTAMP,
    side TEXT,
    entry_price DOUBLE,
    exit_price DOUBLE,
    shares BIGINT,
    gross_pnl DOUBLE,
    fees DOUBLE,
    tax DOUBLE,
    slippage DOUBLE,
    net_pnl DOUBLE,
    gross_return DOUBLE,
    net_return DOUBLE,
    holding_minutes DOUBLE,
    exit_reason TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_name TEXT,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    parameters_json TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS manual_fill_observations (
    observation_id TEXT PRIMARY KEY,
    signal_date DATE,
    profile_name TEXT,
    candidate_hash TEXT,
    symbol TEXT,
    market TEXT,
    name TEXT,
    observed_time TEXT,
    broker TEXT,
    intended_entry_price DOUBLE,
    displayed_best_bid DOUBLE,
    displayed_best_ask DOUBLE,
    displayed_bid_size_shares BIGINT,
    displayed_ask_size_shares BIGINT,
    limit_up_price DOUBLE,
    was_limit_up_locked BOOLEAN,
    was_order_submitted BOOLEAN,
    order_type TEXT,
    order_quantity_shares BIGINT,
    order_price DOUBLE,
    simulated_queue_position BIGINT,
    actual_filled_shares BIGINT,
    actual_avg_fill_price DOUBLE,
    fill_status TEXT,
    reason_not_filled TEXT,
    screenshot_path TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE manual_fill_observations ADD COLUMN IF NOT EXISTS profile_name TEXT;
ALTER TABLE manual_fill_observations ADD COLUMN IF NOT EXISTS candidate_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_daily_prices_trade_date
    ON daily_prices (trade_date, market);

CREATE INDEX IF NOT EXISTS idx_intraday_bars_trade_date
    ON intraday_bars (trade_date, market);

CREATE INDEX IF NOT EXISTS idx_index_daily_prices_trade_date
    ON index_daily_prices (trade_date, index_symbol);

CREATE INDEX IF NOT EXISTS idx_sector_daily_features_trade_date
    ON sector_daily_features (trade_date, sector);

CREATE INDEX IF NOT EXISTS idx_event_candidates_symbol_date
    ON event_candidates (symbol, trade_date);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_strategy_signal
    ON backtest_trades (strategy_name, signal_date);

CREATE INDEX IF NOT EXISTS idx_manual_fill_observations_signal
    ON manual_fill_observations (profile_name, signal_date, symbol, market);
