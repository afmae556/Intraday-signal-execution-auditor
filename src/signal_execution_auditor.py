import re
import json
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_CSV = BASE_DIR / "results" / "execution_audit_report.csv"
SUMMARY_CSV = BASE_DIR / "results" / "session_performance_summary.csv"

ASSETS = ["AUDUSD", "EURUSD", "GBPUSD", "NZDUSD", "USDJPY", "XAUUSD"]
EXECUTION_LATENCY_MINUTES = 2


def parse_unstructured_signals(json_path: Path) -> pd.DataFrame:
    """
    Extracts structured trade parameters (asset, direction, entry, SL, TP1, TP2)
    from unstructured JSON message logs using regular expressions.
    """
    with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    signals = []
    blocks = re.split(r'"date_unixtime":\s*"(\d+)"', content)

    for i in range(1, len(blocks) - 1, 2):
        unixtime = int(blocks[i])
        text_block = blocks[i + 1][:1500].lower().replace("\\n", " ")

        if "buy" not in text_block and "sell" not in text_block:
            continue

        asset_found = next((a for a in ASSETS if a.lower() in text_block), None)
        if not asset_found:
            continue

        entry_match = re.search(r"(?:now|limit|cmp|@)\s*([0-9]+\.?[0-9]*)", text_block)
        sl_match = re.search(r"(?:sl|stop|stop loss)\s*[-:@]?\s*([0-9]+\.?[0-9]*)", text_block)
        tp1_match = re.search(r"tp-?1\s*[-:@]?\s*([0-9]+\.?[0-9]*)", text_block)
        tp2_match = re.search(r"tp-?2\s*[-:@]?\s*([0-9]+\.?[0-9]*|open)", text_block)

        if entry_match and sl_match and tp1_match:
            sig = {
                "timestamp": unixtime,
                "asset": asset_found,
                "direction": "BUY" if "buy" in text_block else "SELL",
                "entry": float(entry_match.group(1)),
                "sl": float(sl_match.group(1)),
                "tp1": float(tp1_match.group(1)),
                "tp2_raw": None,
            }
            if tp2_match:
                val = tp2_match.group(1)
                sig["tp2_raw"] = "OPEN" if val == "open" else float(val)
            signals.append(sig)

    return pd.DataFrame(signals)


def compute_session_statistics(df: pd.DataFrame, session_label: str) -> dict:
    valid_outcomes = ["Stop Loss (SL)", "Take Profit (TP2)", "Break Even (BE)"]
    df_valid = df[df["Resultado"].isin(valid_outcomes)].copy()

    total_trades = len(df_valid)
    typo_errors = len(df[df["Resultado"].str.contains("Typo", case=False, na=False)])
    missed_trades = len(df[df["Resultado"].str.contains("Missed", case=False, na=False)])
    if total_trades == 0:
        return None

    wins = len(df_valid[df_valid["Resultado"] == "Take Profit (TP2)"])
    losses = len(df_valid[df_valid["Resultado"] == "Stop Loss (SL)"])
    bes = len(df_valid[df_valid["Resultado"] == "Break Even (BE)"])

    win_rate = ((wins + bes) / total_trades) * 100.0
    net_r = df_valid["R_Obtenido"].sum()

    avg_win_r = df_valid[df_valid["Resultado"] == "Take Profit (TP2)"]["R_Obtenido"].mean() if wins > 0 else 0.0
    avg_be_r = df_valid[df_valid["Resultado"] == "Break Even (BE)"]["R_Obtenido"].mean() if bes > 0 else 0.0

    df_valid["R_Cum"] = df_valid["R_Obtenido"].cumsum()
    df_valid["Peak"] = df_valid["R_Cum"].cummax()
    df_valid["Drawdown"] = df_valid["R_Cum"] - df_valid["Peak"]
    max_dd = df_valid["Drawdown"].min()

    return {
        "Session": session_label,
        "Executed_Trades": total_trades,
        "Missed_Latency": missed_trades,
        "Filtered_Typos": typo_errors,
        "Win_Rate_Pct": round(win_rate, 2),
        "Full_TP2_Wins": wins,
        "Partial_BE_Wins": bes,
        "Stop_Losses": losses,
        "Avg_RR_TP2": round(avg_win_r, 2),
        "Avg_RR_Partial_BE": round(avg_be_r, 2),
        "Net_R": round(net_r, 2),
        "Max_Drawdown_R": round(max_dd, 2),
    }


if __name__ == "__main__":
    if RESULTS_CSV.exists():
        df_res = pd.read_csv(RESULTS_CSV)
        s_ny = compute_session_statistics(df_res[df_res["Horario"].str.contains("7-11", na=False)], "NY Morning (07:00-11:00 EST)")
        s_rest = compute_session_statistics(df_res[~df_res["Horario"].str.contains("7-11", na=False)], "Off-Peak Sessions")
        s_all = compute_session_statistics(df_res, "Global (24h All Sessions)")

        summary_df = pd.DataFrame([s for s in [s_ny, s_rest, s_all] if s])
        summary_df.to_csv(SUMMARY_CSV, index=False)
        print("=== EXECUTION AUDIT QUANTITATIVE SUMMARY (50% PARTIAL TP1 + BREAK-EVEN + 2-MIN LATENCY) ===")
        print(summary_df.to_string(index=False))
