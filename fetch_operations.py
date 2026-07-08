#!/usr/bin/env python3
"""
fetch_operations.py — история покупок и продаж из Tinkoff Invest API.
Запускается автоматически при изменении qty в портфеле.
Сохраняет в logs/operations_history.json
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
        headers=t_headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.loads(r.read())

def parse_money(m):
    if not m: return 0.0
    return int(m.get("units", 0)) + int(m.get("nano", 0)) / 1e9

# FIGI → тикер маппинг (известные инструменты)
FIGI_MAP = {
    "BBG004730N88": "SBER",
    "BBG000K3STR7": "TGLD",
    "TCS03A108X38": "X5",
    "BBG0063FKTD9": "LENT",
    "BBG000TY1CD1": "BELU",
    "TCS80A101X50": "TGLD",
    "TCS10A101X50": "TGLD",
}

COMPANY_NAMES = {
    "SBER": "Сбербанк",
    "X5":   "ИКС 5",
    "LENT": "Лента",
    "BELU": "НоваБев",
    "TGLD": "Тинькофф Золото",
}

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

# 2. Получаем историю всех операций с 01.01.2024
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

# 3. Разделяем покупки и продажи
BUY_TYPES  = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD"}
SELL_TYPES = {"OPERATION_TYPE_SELL"}
DIV_TYPES  = {"OPERATION_TYPE_DIVIDEND", "OPERATION_TYPE_DIVIDEND_TAX"}

buys  = []
sells = []
divs  = []

# Собираем FIGI из операций для маппинга
unknown_figis = set()

for op in all_ops:
    op_type = op.get("operationType", "")
    inst_type = op.get("instrumentType", "")
    figi = op.get("figi", "")

    if inst_type not in ("share", "etf") and op_type not in DIV_TYPES:
        continue

    date_str = op.get("date", "")
    price    = parse_money(op.get("price"))
    qty      = abs(int(op.get("quantity", 0) or op.get("quantityLots", 0)))
    payment  = parse_money(op.get("payment"))
    ticker   = FIGI_MAP.get(figi, "")

    if not ticker and figi:
        unknown_figis.add(figi)

    record = {
        "date":     date_str[:10],
        "datetime": date_str,
        "figi":     figi,
        "ticker":   ticker,
        "name":     op.get("name", ""),
        "price":    round(price, 4),
        "qty":      qty,
        "total":    round(abs(payment), 2),
        "currency": op.get("currency", "rub"),
    }

    if op_type in BUY_TYPES:
        record["type"] = "buy"
        buys.append(record)
    elif op_type in SELL_TYPES:
        record["type"] = "sell"
        record["total"] = round(abs(payment), 2)
        # Исключаем технические конвертации паёв фонда
        # (старый FIGI BBG000K3STR7 TGLD был конвертирован в TCS80A101X50)
        # Признак: цена пая аномально низкая (< половины от текущей цены)
        # или это известный FIGI конвертации
        CONVERSION_FIGIS = {'BBG000K3STR7'}  # старый FIGI TGLD до конвертации
        if figi in CONVERSION_FIGIS and price < 10.0:
            print(f'  [OPERATIONS] Пропускаем техническую конвертацию: {figi} {qty} шт × {price}₽ (не реальная продажа)')
            record["type"] = "conversion"  # помечаем как конвертацию
        else:
            sells.append(record)
    elif op_type in DIV_TYPES:
        record["type"] = "dividend" if "TAX" not in op_type else "dividend_tax"
        divs.append(record)

print(f"Покупок: {len(buys)}, Продаж: {len(sells)}, Дивидендов: {len(divs)}")

# 4. Маппинг неизвестных FIGI через Tinkoff API
if unknown_figis:
    print(f"Определяем тикеры для {len(unknown_figis)} неизвестных FIGI...")
    for figi in unknown_figis:
        try:
            resp = tinkoff_post(
                "tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
                {"idType": "ID_TYPE_FIGI", "id": figi}
            )
            inst = resp.get("instrument", {})
            tk = inst.get("ticker", figi)
            FIGI_MAP[figi] = tk
            if tk not in COMPANY_NAMES:
                COMPANY_NAMES[tk] = inst.get("name", tk)
            print(f"  {figi} → {tk}")
        except Exception as e:
            FIGI_MAP[figi] = figi
            print(f"  {figi}: ошибка {e}")

# Применяем тикеры к операциям
for op_list in [buys, sells, divs]:
    for op in op_list:
        op["ticker"] = FIGI_MAP.get(op["figi"], op["figi"])
        op["company"] = COMPANY_NAMES.get(op["ticker"], op["ticker"])

# 5. Считаем P&L по продажам (FIFO)
print("Считаем P&L по продажам (FIFO)...")
portfolio_cost = {}  # ticker -> list of (qty, price) FIFO

all_ops_sorted = sorted(buys + sells, key=lambda x: x["datetime"])

for op in all_ops_sorted:
    t = op["ticker"]
    if t not in portfolio_cost:
        portfolio_cost[t] = []

    if op["type"] == "buy":
        portfolio_cost[t].append({"qty": op["qty"], "price": op["price"]})
    elif op["type"] == "sell":
        sell_qty  = op["qty"]
        sell_price = op["price"]
        cost_basis = 0.0
        remaining  = sell_qty

        while remaining > 0 and portfolio_cost.get(t):
            lot = portfolio_cost[t][0]
            take = min(lot["qty"], remaining)
            cost_basis += take * lot["price"]
            remaining  -= take
            lot["qty"] -= take
            if lot["qty"] == 0:
                portfolio_cost[t].pop(0)

        op["cost_basis"]  = round(cost_basis, 2)
        op["pnl"]         = round(op["total"] - cost_basis, 2)
        op["pnl_pct"]     = round((op["pnl"] / cost_basis * 100) if cost_basis else 0, 2)

# 6. Итоги по дивидендам
div_by_ticker = {}
for d in divs:
    if d["type"] == "dividend":
        t = d["ticker"]
        div_by_ticker[t] = div_by_ticker.get(t, 0) + d["total"]

# 7. Сохраняем результат
now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
out = {
    "generated":    now_str,
    "account_id":   account_id,
    "period_from":  "2024-01-01",
    "total_buys":   len(buys),
    "total_sells":  len(sells),
    "total_divs":   len(divs),
    "buys":         sorted(buys, key=lambda x: x["datetime"]),
    "sells":        sorted(sells, key=lambda x: x["datetime"]),
    "dividends":    sorted(divs, key=lambda x: x["datetime"]),
    "dividends_by_ticker": div_by_ticker,
    "realized_pnl": {
        t: round(sum(s["pnl"] for s in sells if s["ticker"] == t and "pnl" in s), 2)
        for t in set(s["ticker"] for s in sells)
    }
}

out_path = LOGS_DIR / "operations_history.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

total_buy  = sum(b["total"] for b in buys)
total_sell = sum(s["total"] for s in sells)
total_div  = sum(d["total"] for d in divs if d["type"] == "dividend")
realized   = sum(s.get("pnl", 0) for s in sells)

print(f"\nИтоги:")
print(f"  Куплено на:    {total_buy:,.0f}₽")
print(f"  Продано на:    {total_sell:,.0f}₽")
print(f"  Дивиденды:     {total_div:,.0f}₽")
print(f"  Реализованный PnL: {realized:+,.0f}₽")
print(f"  Сохранено: {out_path}")
