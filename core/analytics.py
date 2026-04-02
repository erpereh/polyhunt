"""
Analytics — Metricas de performance para el dashboard de PolyHunt.

Calcula:
  - Win rate por modelo (Cerebras vs Groq)
  - Win rate por rango de gap (15-20%, 20-30%, >30%)
  - Tiempo promedio de posicion (dias)
  - Drawdown maximo
  - Sharpe ratio (paper)
  - Estadisticas por dia/semana
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import math

from core.db import get_db

logger = logging.getLogger(__name__)


def get_analytics_data() -> dict:
    """
    Obtiene todas las metricas de analytics en una sola llamada.
    Retorna dict con:
      - win_rate_by_model: {cerebras: float, groq: float}
      - win_rate_by_gap: {gap_15_20: float, gap_20_30: float, gap_30_plus: float}
      - avg_position_days: float
      - max_drawdown: float (porcentaje)
      - sharpe_ratio: float
      - daily_pnl: [{date, pnl, cumulative}]
      - total_trades: int
      - total_wins: int
      - total_losses: int
      - best_trade: float
      - worst_trade: float
      - avg_win: float
      - avg_loss: float
      - profit_factor: float
    """
    db = get_db()
    
    try:
        # Obtener todos los trades cerrados
        trades = (
            db.table("paper_trades")
            .select("*")
            .eq("status", "closed")
            .order("closed_at", desc=False)
            .execute()
            .data or []
        )
        
        # Obtener cuenta para calcular drawdown
        account = db.table("account").select("*").eq("id", 1).single().execute().data
        initial_balance = float(account.get("initial_balance", 10000) if account else 10000)
        
        # Obtener analisis para mapear modelo a trades
        analyses = (
            db.table("llm_analyses")
            .select("market_id, model, gap")
            .order("timestamp", desc=True)
            .execute()
            .data or []
        )
        
        # Crear mapa de market_id -> ultimo analisis
        analysis_map = {}
        for a in analyses:
            mid = a.get("market_id")
            if mid and mid not in analysis_map:
                analysis_map[mid] = a
        
        # Calcular metricas
        result = {
            "win_rate_by_model": _calc_win_rate_by_model(trades, analysis_map),
            "win_rate_by_gap": _calc_win_rate_by_gap(trades, analysis_map),
            "avg_position_days": _calc_avg_position_days(trades),
            "max_drawdown": _calc_max_drawdown(trades, initial_balance),
            "sharpe_ratio": _calc_sharpe_ratio(trades, initial_balance),
            "daily_pnl": _calc_daily_pnl(trades, initial_balance),
            **_calc_trade_stats(trades),
        }
        
        return result
        
    except Exception as e:
        logger.error(f"[Analytics] Error calculando metricas: {e}")
        return {
            "win_rate_by_model": {"cerebras": 0, "groq": 0},
            "win_rate_by_gap": {"gap_15_20": 0, "gap_20_30": 0, "gap_30_plus": 0},
            "avg_position_days": 0,
            "max_drawdown": 0,
            "sharpe_ratio": 0,
            "daily_pnl": [],
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
        }


def _calc_win_rate_by_model(trades: list, analysis_map: dict) -> dict:
    """Calcula win rate separado por modelo LLM."""
    model_stats = {
        "cerebras": {"wins": 0, "total": 0},
        "groq": {"wins": 0, "total": 0},
    }
    
    for t in trades:
        market_id = t.get("market_id")
        pnl = float(t.get("pnl") or 0)
        
        analysis = analysis_map.get(market_id)
        if not analysis:
            continue
            
        model = (analysis.get("model") or "").lower()
        
        # Determinar modelo
        if "cerebras" in model or "qwen" in model:
            key = "cerebras"
        elif "groq" in model or "llama" in model:
            key = "groq"
        else:
            continue
        
        model_stats[key]["total"] += 1
        if pnl > 0:
            model_stats[key]["wins"] += 1
    
    return {
        "cerebras": _safe_rate(model_stats["cerebras"]["wins"], model_stats["cerebras"]["total"]),
        "groq": _safe_rate(model_stats["groq"]["wins"], model_stats["groq"]["total"]),
    }


def _calc_win_rate_by_gap(trades: list, analysis_map: dict) -> dict:
    """Calcula win rate por rango de gap."""
    gap_stats = {
        "gap_15_20": {"wins": 0, "total": 0},
        "gap_20_30": {"wins": 0, "total": 0},
        "gap_30_plus": {"wins": 0, "total": 0},
    }
    
    for t in trades:
        market_id = t.get("market_id")
        pnl = float(t.get("pnl") or 0)
        
        # Usar gap_at_entry del trade si existe, sino del analisis
        gap = float(t.get("gap_at_entry") or 0)
        if not gap:
            analysis = analysis_map.get(market_id)
            if analysis:
                gap = float(analysis.get("gap") or 0)
        
        if not gap:
            continue
        
        gap_pct = abs(gap) * 100
        
        if 15 <= gap_pct < 20:
            key = "gap_15_20"
        elif 20 <= gap_pct < 30:
            key = "gap_20_30"
        elif gap_pct >= 30:
            key = "gap_30_plus"
        else:
            continue
        
        gap_stats[key]["total"] += 1
        if pnl > 0:
            gap_stats[key]["wins"] += 1
    
    return {
        "gap_15_20": _safe_rate(gap_stats["gap_15_20"]["wins"], gap_stats["gap_15_20"]["total"]),
        "gap_20_30": _safe_rate(gap_stats["gap_20_30"]["wins"], gap_stats["gap_20_30"]["total"]),
        "gap_30_plus": _safe_rate(gap_stats["gap_30_plus"]["wins"], gap_stats["gap_30_plus"]["total"]),
    }


def _calc_avg_position_days(trades: list) -> float:
    """Calcula tiempo promedio de posicion en dias."""
    durations = []
    
    for t in trades:
        opened_at = t.get("opened_at")
        closed_at = t.get("closed_at")
        
        if not opened_at or not closed_at:
            continue
        
        try:
            opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
            days = (closed - opened).total_seconds() / 86400
            durations.append(max(0, days))
        except Exception:
            continue
    
    if not durations:
        return 0
    
    return round(sum(durations) / len(durations), 1)


def _calc_max_drawdown(trades: list, initial_balance: float) -> float:
    """
    Calcula el drawdown maximo historico.
    Drawdown = (peak - trough) / peak * 100
    """
    if not trades:
        return 0
    
    # Reconstruir equity curve
    balance = initial_balance
    peak = initial_balance
    max_dd = 0
    
    for t in trades:
        pnl = float(t.get("pnl") or 0)
        balance += pnl
        
        if balance > peak:
            peak = balance
        
        if peak > 0:
            dd = (peak - balance) / peak * 100
            max_dd = max(max_dd, dd)
    
    return round(max_dd, 2)


def _calc_sharpe_ratio(trades: list, initial_balance: float, risk_free_rate: float = 0.05) -> float:
    """
    Calcula Sharpe Ratio simplificado.
    Sharpe = (mean_return - risk_free) / std_return
    Asume periodicidad diaria, anualiza con sqrt(252).
    """
    if len(trades) < 2:
        return 0
    
    # Calcular retornos porcentuales por trade
    returns = []
    balance = initial_balance
    
    for t in trades:
        pnl = float(t.get("pnl") or 0)
        if balance > 0:
            ret = pnl / balance
            returns.append(ret)
        balance += pnl
    
    if len(returns) < 2:
        return 0
    
    # Media y desviacion estandar
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    std_ret = math.sqrt(variance) if variance > 0 else 0
    
    if std_ret == 0:
        return 0
    
    # Ajustar risk-free rate a por-trade (aproximado)
    # Asumiendo ~1 trade por dia en promedio
    rf_per_trade = risk_free_rate / 252
    
    sharpe = (mean_ret - rf_per_trade) / std_ret
    
    # Anualizar (sqrt de trades esperados por ano)
    # Aproximamos con sqrt(252) para comparabilidad
    sharpe_annualized = sharpe * math.sqrt(min(len(trades), 252))
    
    return round(sharpe_annualized, 2)


def _calc_daily_pnl(trades: list, initial_balance: float) -> list:
    """
    Agrupa P&L por dia para grafico de equity.
    Retorna lista de {date, pnl, cumulative}.
    """
    if not trades:
        return []
    
    # Agrupar por fecha
    daily = {}
    
    for t in trades:
        closed_at = t.get("closed_at")
        pnl = float(t.get("pnl") or 0)
        
        if not closed_at:
            continue
        
        try:
            dt = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
            date_key = dt.strftime("%Y-%m-%d")
            daily[date_key] = daily.get(date_key, 0) + pnl
        except Exception:
            continue
    
    if not daily:
        return []
    
    # Ordenar por fecha y calcular acumulado
    sorted_dates = sorted(daily.keys())
    result = []
    cumulative = 0
    
    for date in sorted_dates:
        pnl = daily[date]
        cumulative += pnl
        result.append({
            "date": date,
            "pnl": round(pnl, 2),
            "cumulative": round(cumulative, 2),
        })
    
    return result


def _calc_trade_stats(trades: list) -> dict:
    """Calcula estadisticas generales de trades."""
    if not trades:
        return {
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "best_trade": 0,
            "worst_trade": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
        }
    
    pnls = [float(t.get("pnl") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    total_wins = sum(wins) if wins else 0
    total_losses = abs(sum(losses)) if losses else 0
    
    return {
        "total_trades": len(trades),
        "total_wins": len(wins),
        "total_losses": len(losses),
        "best_trade": round(max(pnls), 2) if pnls else 0,
        "worst_trade": round(min(pnls), 2) if pnls else 0,
        "avg_win": round(total_wins / len(wins), 2) if wins else 0,
        "avg_loss": round(total_losses / len(losses), 2) if losses else 0,
        "profit_factor": round(total_wins / total_losses, 2) if total_losses > 0 else (999 if total_wins > 0 else 0),
    }


def get_market_price_history(market_id: str, limit: int = 100) -> list:
    """
    Obtiene historial de precios de un mercado para grafico.
    Retorna lista de {timestamp, price, volume}.
    """
    db = get_db()
    
    try:
        snapshots = (
            db.table("price_snapshots")
            .select("timestamp, price, volume")
            .eq("market_id", market_id)
            .order("timestamp", desc=False)
            .limit(limit)
            .execute()
            .data or []
        )
        
        return [
            {
                "timestamp": s.get("timestamp"),
                "price": float(s.get("price") or 0),
                "volume": float(s.get("volume") or 0),
            }
            for s in snapshots
        ]
        
    except Exception as e:
        logger.error(f"[Analytics] Error obteniendo historial de {market_id}: {e}")
        return []


def get_resolution_calendar(days_ahead: int = 30) -> list:
    """
    Obtiene mercados proximos a resolver para calendario.
    Retorna lista ordenada por end_date de mercados activos.
    """
    db = get_db()
    
    try:
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=days_ahead)
        
        markets = (
            db.table("markets")
            .select("id, question, end_date, last_price, volume")
            .gte("end_date", now.isoformat())
            .lte("end_date", future.isoformat())
            .order("end_date", desc=False)
            .limit(50)
            .execute()
            .data or []
        )
        
        # Verificar posiciones abiertas
        positions = (
            db.table("positions")
            .select("market_id")
            .execute()
            .data or []
        )
        position_markets = {p["market_id"] for p in positions}
        
        result = []
        for m in markets:
            end_date = m.get("end_date")
            if not end_date:
                continue
            
            try:
                end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
                days_left = (end_dt - now).days
            except Exception:
                days_left = 0
            
            result.append({
                "id": m.get("id"),
                "question": m.get("question"),
                "end_date": end_date,
                "days_left": max(0, days_left),
                "last_price": float(m.get("last_price") or 0),
                "volume": float(m.get("volume") or 0),
                "has_position": m.get("id") in position_markets,
            })
        
        return result
        
    except Exception as e:
        logger.error(f"[Analytics] Error obteniendo calendario: {e}")
        return []


def _safe_rate(wins: int, total: int) -> float:
    """Calcula porcentaje de forma segura."""
    if total == 0:
        return 0
    return round((wins / total) * 100, 1)
