-- Initial schema. Managed by Alembic in CI; this SQL mirrors the first
-- migration for docker-entrypoint-initdb.d bootstrap.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT        NOT NULL,
    interval    TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL,
    raw_open    DOUBLE PRECISION,   -- unadjusted preserved (MVP §11.7)
    raw_close   DOUBLE PRECISION,
    PRIMARY KEY (symbol, interval, ts)
);
SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS regime_states (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT        NOT NULL,
    regime      TEXT        NOT NULL,
    as_of       TIMESTAMPTZ NOT NULL,
    metrics     JSONB       NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS signals (
    id            BIGSERIAL PRIMARY KEY,
    strategy_id   TEXT        NOT NULL,
    symbol        TEXT        NOT NULL,
    direction     TEXT        NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL,
    bar_time      TIMESTAMPTZ NOT NULL,
    validated     BOOLEAN     NOT NULL,
    score         DOUBLE PRECISION,
    regime        TEXT,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS validation_funnel (
    id            BIGSERIAL PRIMARY KEY,
    signal_id     BIGINT REFERENCES signals(id),
    strategy_id   TEXT        NOT NULL,
    symbol        TEXT        NOT NULL,
    stage         TEXT        NOT NULL,
    passed        BOOLEAN     NOT NULL,
    measured      JSONB       NOT NULL DEFAULT '{}',
    reason        TEXT        NOT NULL DEFAULT '',
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_funnel_stage ON validation_funnel (stage, passed);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,   -- idempotency key
    broker_order_id TEXT,
    strategy_id     TEXT        NOT NULL,
    symbol          TEXT        NOT NULL,
    side            TEXT        NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    order_type      TEXT        NOT NULL,
    limit_price     DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION NOT NULL,
    take_profit     DOUBLE PRECISION,
    status          TEXT        NOT NULL,
    filled_qty      DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_fill_price  DOUBLE PRECISION,
    metadata        JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fills (
    id              BIGSERIAL PRIMARY KEY,
    client_order_id TEXT REFERENCES orders(client_order_id),
    symbol          TEXT        NOT NULL,
    side            TEXT        NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    commission      DOUBLE PRECISION NOT NULL DEFAULT 0,
    at              TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id           BIGSERIAL PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    strategy_id  TEXT        NOT NULL,
    qty          DOUBLE PRECISION NOT NULL,
    entry_price  DOUBLE PRECISION NOT NULL,
    exit_price   DOUBLE PRECISION NOT NULL,
    entry_at     TIMESTAMPTZ NOT NULL,
    exit_at      TIMESTAMPTZ NOT NULL,
    pnl          DOUBLE PRECISION NOT NULL,
    commission   DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    at      TIMESTAMPTZ PRIMARY KEY,
    equity  DOUBLE PRECISION NOT NULL,
    cash    DOUBLE PRECISION NOT NULL,
    daily_pnl DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS journal (
    id      BIGSERIAL PRIMARY KEY,
    at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind    TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_journal_kind ON journal (kind, at);

CREATE TABLE IF NOT EXISTS delistings (
    symbol      TEXT PRIMARY KEY,
    delisted_at DATE NOT NULL
);
