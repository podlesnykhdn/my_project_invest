#!/usr/bin/env python3
"""
fetch_operations.py — получает историю операций из Tinkoff Invest API
с 01.01.2024 и сохраняет в logs/operations_history.json
"""
import os, json, ssl, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TINKOFF_TOKEN = os.environ.get("TINKOFF_TOKEN")
if not TINKOFF_TOKEN:
    print("TINKOFF_TOKEN не найден")
    exit(1)

BASE_URL = "https://invest-public-api.tbank.ru/rest"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

t_headers = {
    "Authorization": f"Bearer {TINKOFF_TOKEN}",
    "Content-Type": "application/json",
}

def tinkoff_post(endpoint, body):
    req = urllib.request.Request(
        f"{BASE_URL}/{endpoint}",
        data=json.dumps(body).encode(),
        headers=t_headers,
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.loads(r.read())

def parse_money(m):
    if not m: return 0.0
    return int(m.get("units", 0)) + int(m.get("nano", 0)) / 1e9

# 1. Получаем account_id
print("Получаем счета...")
accounts = tinkoff_post(
    "tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts", {}
)
account_id = None
for acc in accounts.get("accounts", []):
    if acc.get("status") == "ACCOUNT_STATUS_OPEN":
        account_id = acc["id"]
        print(f"  Счёт: {acc.get('name', account_id)}")
        break

if not account_id:
    print("Счёт не найден")
    exit(1)

# 2. Получаем историю операций с 01.01.2024
print("Загружаем историю операций с 01.01.2024...")
operations = tinkoff_post(
    "tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
    {
        "accountId": account_id,
        "from": "2024-01-01T00:00:00Z",
        "to": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": "OPERATION_STATE_EXECUTED",
    }
)

all_ops = operations.get("operations", [])
print(f"Всего операций: {len(all_ops)}")

# 3. Фильтруем только покупки акций
BUY_TYPES = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD"}
buys = []
for op in all_ops:
    op_type = op.get("operationType", "")
    if op_type not in BUY_TYPES:
        continue
    if op.get("instrumentType") not in ("share", "etf"):
        continue

    ticker = op.get("figi", "")
    date_str = op.get("date", "")
    price = parse_money(op.get("price"))
    qty = int(op.get("quantity", 0) or op.get("quantityLots", 0))
    payment = abs(parse_money(op.get("payment")))

    buys.append({
        "date": date_str[:10],
        "datetime": date_str,
        "ticker": ticker,
        "figi": op.get("figi", ""),
        "name": op.get("name", ""),
        "price": round(price, 2),
        "qty": qty,
        "total": round(payment, 2),
        "currency": op.get("currency", "rub"),
    })

print(f"Покупок акций и ETF: {len(buys)}")

# 4. Маппинг FIGI → тикер через InstrumentsService
figis = list({b["figi"] for b in buys if b["figi"]})
figi_to_ticker = {}
if figis:
    print(f"Получаем тикеры для {len(figis)} инструментов...")
    for figi in figis:
        try:
            resp = tinkoff_post(
                "tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
                {"idType": "ID_TYPE_FIGI", "id": figi}
            )
            inst = resp.get("instrument", {})
            tk = inst.get("ticker", figi)
            figi_to_ticker[figi] = tk
        except Exception as e:
            figi_to_ticker[figi] = figi
            print(f"  Ошибка для {figi}: {e}")

for b in buys:
    b["ticker"] = figi_to_ticker.get(b["figi"], b["ticker"])

# 5. Сохраняем
out = {
    "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "account_id": account_id,
    "period_from": "2024-01-01",
    "total_buys": len(buys),
    "operations": sorted(buys, key=lambda x: x["datetime"]),
}

out_path = LOGS_DIR / "operations_history.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"Сохранено: {out_path}")
print(f"Уникальных тикеров: {len(set(b['ticker'] for b in buys))}")
