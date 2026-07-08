#!/usr/bin/env python3
"""
get_historical_prices.py — получает цены закрытия на конкретную дату через Tinkoff API
"""
import os, json, ssl, urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

BASE_DIR = Path(__file__).parent
TINKOFF_TOKEN = os.environ.get("TINKOFF_TOKEN")
if not TINKOFF_TOKEN:
    print("TINKOFF_TOKEN не найден"); exit(1)

BASE_URL = "https://invest-public-api.tbank.ru/rest"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

headers = {"Authorization": f"Bearer {TINKOFF_TOKEN}", "Content-Type": "application/json"}

def tpost(endpoint, body):
    req = urllib.request.Request(
        f"{BASE_URL}/{endpoint}",
        data=json.dumps(body).encode(),
        headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.loads(r.read())

def parse_money(m):
    if not m: return 0.0
    return int(m.get("units", 0)) + int(m.get("nano", 0)) / 1e9

# Маппинг тикер -> FIGI (актуальный)
INSTRUMENTS = {
    "SBER": "BBG004730N88",
    "X5":   "TCS03A108X38",
    "LENT": "BBG0063FKTD9",
    "BELU": "BBG000TY1CD1",
    "TGLD": "TCS80A101X50",
}

TARGET_DATE = "2026-05-01"
# 1 мая — выходной, берём период 29 апр — 6 мая
FROM_DT = "2026-04-28T00:00:00Z"
TO_DT   = "2026-05-07T00:00:00Z"

print(f"Запрашиваем цены закрытия на {TARGET_DATE} из Tinkoff API...")
print()

prices_on_date = {}

for ticker, figi in INSTRUMENTS.items():
    try:
        resp = tpost(
            "tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
            {
                "figi": figi,
                "from": FROM_DT,
                "to":   TO_DT,
                "interval": "CANDLE_INTERVAL_DAY",
            }
        )
        candles = resp.get("candles", [])
        # Берём последнюю свечу до 1 мая включительно
        best = None
        for c in candles:
            dt = c.get("time", "")[:10]  # YYYY-MM-DD
            if dt <= TARGET_DATE:
                best = c
        if best:
            close = parse_money(best.get("close"))
            dt = best.get("time", "")[:10]
            prices_on_date[ticker] = {"price": round(close, 2), "date": dt}
            print(f"  {ticker}: {close:.2f}₽ (дата: {dt})")
        else:
            print(f"  {ticker}: нет данных в диапазоне")
    except Exception as e:
        print(f"  {ticker}: ошибка — {e}")

# Сохраняем результат
out = {"target_date": TARGET_DATE, "prices": prices_on_date}
with open(BASE_DIR / "logs" / "prices_20260501.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\nСохранено в logs/prices_20260501.json")
