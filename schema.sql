-- ============================================================
-- PostgreSQL Schema for Trading System
-- Run once on Cloud SQL instance
-- psql -h 34.93.21.87 -U trading_user -d trading -f schema.sql
-- ============================================================

-- ── signals ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    signal_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_name       TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    signal_time_ist     TIMESTAMP NOT NULL,
    entry_time_exec_ist TIMESTAMP,
    stoploss            FLOAT,
    atr_pct             FLOAT,
    bb_width            FLOAT,
    vwap_dist           FLOAT,
    rsi_drop            FLOAT,
    rr                  FLOAT,
    target              FLOAT,
    c1_rvol             FLOAT,
    c2_rvol             FLOAT,
    signal_confidence   FLOAT,
    signal_status       TEXT NOT NULL DEFAULT 'PENDING',
    created_at_ist      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at_ist      TIMESTAMP NOT NULL DEFAULT NOW(),

    -- dedup constraint
    UNIQUE (strategy_name, strategy_version, timeframe, symbol, signal_time_ist)
);

CREATE INDEX IF NOT EXISTS idx_signals_status
    ON signals (signal_status);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_status
    ON signals (symbol, signal_status);

CREATE INDEX IF NOT EXISTS idx_signals_created
    ON signals (created_at_ist DESC);

-- ── open_trades ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS open_trades (
    trade_id            UUID PRIMARY KEY,
    signal_id           UUID REFERENCES signals(signal_id),
    strategy_name       TEXT,
    timeframe           TEXT,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    execution_mode      TEXT,           -- 'PAPER' | 'LIVE'
    broker_order_id     TEXT,           -- Kite order ID

    entry_time          TIMESTAMP,
    entry_candle_time   TIMESTAMP,
    entry_price         FLOAT,
    quantity            FLOAT,
    stoploss            FLOAT,
    target              FLOAT,

    atr                 FLOAT,
    trail_active        BOOLEAN DEFAULT FALSE,
    trail_low           FLOAT,

    rr                  FLOAT,
    atr_pct             FLOAT,
    rsi                 FLOAT,
    bb_width            FLOAT,
    vwap                FLOAT,
    close_vs_vwap_pct   FLOAT,

    status              TEXT NOT NULL DEFAULT 'OPEN',
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_open_trades_status
    ON open_trades (status);

CREATE INDEX IF NOT EXISTS idx_open_trades_symbol_status
    ON open_trades (symbol, status);

CREATE INDEX IF NOT EXISTS idx_open_trades_execution_mode
    ON open_trades (execution_mode, status);

-- ── closed_trades (paper_trades equivalent) ──────────────────
CREATE TABLE IF NOT EXISTS closed_trades (
    trade_id            UUID PRIMARY KEY,
    signal_id           UUID,
    strategy_name       TEXT,
    timeframe           TEXT,
    symbol              TEXT,
    side                TEXT,
    execution_mode      TEXT,
    broker_order_id     TEXT,

    entry_time          TIMESTAMP,
    entry_price         FLOAT,
    quantity            FLOAT,
    stoploss            FLOAT,
    target              FLOAT,

    exit_time           TIMESTAMP,
    exit_price          FLOAT,
    exit_reason         TEXT,
    pnl                 FLOAT,
    r_multiple          FLOAT,

    rr                  FLOAT,
    atr                 FLOAT,
    atr_pct             FLOAT,
    rsi                 FLOAT,
    bb_width            FLOAT,
    vwap                FLOAT,
    close_vs_vwap_pct   FLOAT,

    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol
    ON closed_trades (symbol);

CREATE INDEX IF NOT EXISTS idx_closed_trades_exit_time
    ON closed_trades (exit_time DESC);

CREATE INDEX IF NOT EXISTS idx_closed_trades_execution_mode
    ON closed_trades (execution_mode, exit_time DESC);
