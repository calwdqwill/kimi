"""
Backtesting engine for mean-reversion spread strategy.

The strategy assumes the spread between Hyperliquid and MOEX closes is mean-reverting:
- Enter long spread  (buy HL / sell MOEX) when Z-Score <= -entry_z
- Enter short spread (sell HL / buy MOEX) when Z-Score >= +entry_z
- Exit when spread reverts to exit_z (partial mean reversion)
- Stop loss when Z-Score moves further against position by stop_z
- Hard time stop after max_hold candles
"""

from dataclasses import dataclass, field
from statistics import mean, stdev
from typing import Optional

from domain import spread


@dataclass
class BacktestParams:
    """Parameters for one backtest run."""

    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.0
    max_hold: int = 48
    lookback: int = 120
    position_size: float = 10000.0
    moex_fee_pct: float = 0.02
    hl_fee_pct: float = 0.035
    slippage_pct: float = 0.03


@dataclass
class Trade:
    """One simulated round-trip trade."""

    entry_ts: int
    exit_ts: int
    side: str
    entry_spread: float
    exit_spread: float
    entry_z: float
    exit_z: float
    pnl: float
    fees: float
    reason: str
    hold_candles: int


@dataclass
class BacktestResult:
    """Result of a backtest run."""

    params: BacktestParams
    total_pnl: float
    num_trades: int
    winrate: float
    avg_pnl: float
    best_trade: float
    worst_trade: float
    max_drawdown: float
    sharpe: float
    trades: list[Trade] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)


def _rolling_zscore(values: list[float], window: int) -> list[Optional[float]]:
    """Return rolling Z-score for each value using the previous `window` values."""
    result: list[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
            continue
        window_vals = values[i - window + 1 : i + 1]
        m = mean(window_vals)
        try:
            sd = stdev(window_vals)
        except Exception:
            sd = 0.0
        if sd == 0:
            result.append(0.0)
        else:
            result.append((values[i] - m) / sd)
    return result


def _compute_max_drawdown(equity_values: list[float]) -> float:
    """Return maximum peak-to-trough drawdown as a positive number."""
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_dd = 0.0
    for val in equity_values:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _compute_sharpe(equity_values: list[float]) -> float:
    """Return annualized Sharpe-like ratio from equity curve (assumes candles are 5m)."""
    if len(equity_values) < 2:
        return 0.0
    returns = [equity_values[i] - equity_values[i - 1] for i in range(1, len(equity_values))]
    avg_return = mean(returns)
    try:
        sd_return = stdev(returns)
    except Exception:
        sd_return = 0.0
    if sd_return == 0:
        return 0.0
    # 5m candles -> 288 per day, sqrt(252) annualization
    return round((avg_return / sd_return) * (288 ** 0.5), 4)


def _round_trade(t: Trade) -> dict:
    """Serialize a Trade to a plain dict."""
    return {
        "entry_ts": t.entry_ts,
        "exit_ts": t.exit_ts,
        "side": t.side,
        "entry_spread": round(t.entry_spread, 4),
        "exit_spread": round(t.exit_spread, 4),
        "entry_z": round(t.entry_z, 4),
        "exit_z": round(t.exit_z, 4),
        "pnl": round(t.pnl, 2),
        "fees": round(t.fees, 2),
        "reason": t.reason,
        "hold_candles": t.hold_candles,
    }


def run_backtest(synced_data: list[dict], params: BacktestParams) -> BacktestResult:
    """
    Run mean-reversion backtest on synchronized candle data.

    synced_data: list of dicts with keys timestamp_ms, moex_close, hl_close (oldest first)
    """
    if not synced_data:
        return BacktestResult(params=params, total_pnl=0.0, num_trades=0, winrate=0.0,
                              avg_pnl=0.0, best_trade=0.0, worst_trade=0.0,
                              max_drawdown=0.0, sharpe=0.0)

    # 1. Compute spread series
    spread_values: list[Optional[float]] = []
    for row in synced_data:
        sp = spread.historical_spread_pct(row["hl_close"], row["moex_close"])
        spread_values.append(sp)

    # Drop rows where spread is None (should not happen with valid closes)
    clean_data: list[dict] = []
    clean_spreads: list[float] = []
    for row, sp in zip(synced_data, spread_values):
        if sp is not None:
            clean_data.append(row)
            clean_spreads.append(sp)

    if not clean_spreads:
        return BacktestResult(params=params, total_pnl=0.0, num_trades=0, winrate=0.0,
                              avg_pnl=0.0, best_trade=0.0, worst_trade=0.0,
                              max_drawdown=0.0, sharpe=0.0)

    # 2. Rolling Z-score
    zscores = _rolling_zscore(clean_spreads, params.lookback)

    # 3. Simulation
    trades: list[Trade] = []
    equity: list[dict] = []
    position: Optional[dict] = None
    total_pnl = 0.0

    one_way_fee_pct = (params.moex_fee_pct + params.hl_fee_pct + 2 * params.slippage_pct) / 100.0
    round_trip_fees = params.position_size * one_way_fee_pct * 2

    for i in range(len(clean_data)):
        ts = clean_data[i]["timestamp_ms"]
        sp = clean_spreads[i]
        z = zscores[i]

        if position is None and z is not None:
            if z >= params.entry_z:
                position = {
                    "side": "short_spread",
                    "entry_ts": ts,
                    "entry_spread": sp,
                    "entry_z": z,
                    "hold": 0,
                }
            elif z <= -params.entry_z:
                position = {
                    "side": "long_spread",
                    "entry_ts": ts,
                    "entry_spread": sp,
                    "entry_z": z,
                    "hold": 0,
                }
        elif position:
            position["hold"] += 1
            close_reason: Optional[str] = None

            if position["side"] == "long_spread":
                if z is not None and z >= -params.exit_z:
                    close_reason = "mean_reversion"
                elif z is not None and z <= -params.stop_z:
                    close_reason = "stop_loss"
            else:  # short_spread
                if z is not None and z <= params.exit_z:
                    close_reason = "mean_reversion"
                elif z is not None and z >= params.stop_z:
                    close_reason = "stop_loss"

            if position["hold"] >= params.max_hold:
                close_reason = "max_hold"

            if close_reason:
                if position["side"] == "long_spread":
                    raw_pnl = (sp - position["entry_spread"]) / 100.0 * params.position_size
                else:
                    raw_pnl = (position["entry_spread"] - sp) / 100.0 * params.position_size

                pnl = raw_pnl - round_trip_fees
                total_pnl += pnl

                trades.append(Trade(
                    entry_ts=position["entry_ts"],
                    exit_ts=ts,
                    side=position["side"],
                    entry_spread=position["entry_spread"],
                    exit_spread=sp,
                    entry_z=position["entry_z"],
                    exit_z=z if z is not None else 0.0,
                    pnl=pnl,
                    fees=round_trip_fees,
                    reason=close_reason,
                    hold_candles=position["hold"],
                ))
                position = None

        equity.append({"timestamp_ms": ts, "equity": round(total_pnl, 2)})

    # 4. Statistics
    num_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    winrate = len(wins) / num_trades if num_trades else 0.0
    avg_pnl = mean([t.pnl for t in trades]) if trades else 0.0
    best_trade = max((t.pnl for t in trades), default=0.0)
    worst_trade = min((t.pnl for t in trades), default=0.0)
    equity_values = [e["equity"] for e in equity]
    max_dd = _compute_max_drawdown(equity_values)
    sharpe = _compute_sharpe(equity_values)

    return BacktestResult(
        params=params,
        total_pnl=round(total_pnl, 2),
        num_trades=num_trades,
        winrate=round(winrate, 4),
        avg_pnl=round(avg_pnl, 2),
        best_trade=round(best_trade, 2),
        worst_trade=round(worst_trade, 2),
        max_drawdown=round(max_dd, 2),
        sharpe=sharpe,
        trades=trades,
        equity=equity,
    )


def optimize_backtest(
    synced_data: list[dict],
    param_grid: dict,
    objective: str = "sharpe",
) -> dict:
    """
    Grid-search optimization over parameter combinations.

    param_grid example:
        {
            "entry_z": [1.5, 2.0, 2.5],
            "exit_z": [0.0, 0.5, 1.0],
            "stop_z": [2.5, 3.0, 3.5],
            "max_hold": [24, 48, 96],
            "lookback": [60, 120, 240],
        }

    objective: one of "sharpe", "total_pnl", "winrate", "pnl_over_dd"
    """
    from itertools import product

    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]

    best_result: Optional[BacktestResult] = None
    best_score = float("-inf")
    all_results: list[dict] = []

    for combo in product(*values):
        kwargs = dict(zip(keys, combo))
        params = BacktestParams(**kwargs)
        result = run_backtest(synced_data, params)

        if objective == "sharpe":
            score = result.sharpe
        elif objective == "total_pnl":
            score = result.total_pnl
        elif objective == "winrate":
            score = result.winrate
        elif objective == "pnl_over_dd":
            score = result.total_pnl / (result.max_drawdown + 1e-9)
        else:
            score = result.sharpe

        summary = {
            "params": kwargs,
            "total_pnl": result.total_pnl,
            "num_trades": result.num_trades,
            "winrate": result.winrate,
            "max_drawdown": result.max_drawdown,
            "sharpe": result.sharpe,
            "score": round(score, 4),
        }
        all_results.append(summary)

        if score > best_score:
            best_score = score
            best_result = result

    if best_result is None:
        return {"best": None, "all": []}

    return {
        "best": {
            "params": {
                "entry_z": best_result.params.entry_z,
                "exit_z": best_result.params.exit_z,
                "stop_z": best_result.params.stop_z,
                "max_hold": best_result.params.max_hold,
                "lookback": best_result.params.lookback,
                "position_size": best_result.params.position_size,
                "moex_fee_pct": best_result.params.moex_fee_pct,
                "hl_fee_pct": best_result.params.hl_fee_pct,
                "slippage_pct": best_result.params.slippage_pct,
            },
            "total_pnl": best_result.total_pnl,
            "num_trades": best_result.num_trades,
            "winrate": best_result.winrate,
            "avg_pnl": best_result.avg_pnl,
            "best_trade": best_result.best_trade,
            "worst_trade": best_result.worst_trade,
            "max_drawdown": best_result.max_drawdown,
            "sharpe": best_result.sharpe,
            "trades": [_round_trade(t) for t in best_result.trades[:50]],
            "equity": best_result.equity,
        },
        "all": sorted(all_results, key=lambda x: x["score"], reverse=True)[:20],
    }


def result_to_dict(result: BacktestResult, include_equity: bool = True) -> dict:
    """Serialize BacktestResult to JSON-friendly dict."""
    out = {
        "params": {
            "entry_z": result.params.entry_z,
            "exit_z": result.params.exit_z,
            "stop_z": result.params.stop_z,
            "max_hold": result.params.max_hold,
            "lookback": result.params.lookback,
            "position_size": result.params.position_size,
            "moex_fee_pct": result.params.moex_fee_pct,
            "hl_fee_pct": result.params.hl_fee_pct,
            "slippage_pct": result.params.slippage_pct,
        },
        "total_pnl": result.total_pnl,
        "num_trades": result.num_trades,
        "winrate": result.winrate,
        "avg_pnl": result.avg_pnl,
        "best_trade": result.best_trade,
        "worst_trade": result.worst_trade,
        "max_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "trades": [_round_trade(t) for t in result.trades[:100]],
    }
    if include_equity:
        out["equity"] = result.equity
    return out
