#!/usr/bin/env python3
"""
update_dashboard.py — обновляет _E в index.html актуальными данными из логов.
Запускается автоматически после каждого сбора данных (bots.yml).
"""
import json, re, sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

BASE_DIR = Path(__file__).parent
TODAY = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
TODAY_D = date.fromisoformat(TODAY)

# Читаем свежий лог
log_file = BASE_DIR / "logs" / "collector" / f"{TODAY}.json"
if not log_file.exists():
    print(f"Лог {TODAY} не найден")
    sys.exit(0)

with open(log_file, encoding="utf-8") as f:
    ld = json.load(f)

tp = ld.get("tinkoff_portfolio", {})
if not tp:
    print("tinkoff_portfolio пустой")
    sys.exit(0)

# Читаем operations_history
ops_file = BASE_DIR / "logs" / "operations_history.json"
buys, sells, divs = [], [], {}
if ops_file.exists():
    with open(ops_file, encoding="utf-8") as f:
        ops = json.load(f)
    MY = {"SBER","X5","LENT","BELU","TGLD"}
    buys  = [b for b in ops.get("buys",[])  if b.get("ticker") in MY]
    sells = [s for s in ops.get("sells",[]) if s.get("ticker") in MY]
    divs  = ops.get("dividends_by_ticker", {})

# Ставки Сбера
RATES = [
    (date(2024,1,1),10.0),(date(2024,9,20),12.0),(date(2024,10,29),13.5),
    (date(2025,2,22),12.0),(date(2025,6,9),10.0),(date(2025,9,1),9.0),
    (date(2025,12,20),9.0),(date(2026,1,5),8.0),(date(2026,2,20),7.5),
    (date(2026,4,1),7.0),(date(2026,4,30),6.5),
]
def get_rate(d):
    rate = RATES[0][1]
    for rd, r in RATES:
        if d >= rd: rate = r
        else: break
    return rate

for b in buys: b["rate"] = get_rate(date.fromisoformat(b["date"]))
for s in sells: s["rate"] = get_rate(date.fromisoformat(s["date"]))

# Deposit comparison
total_spent = sum(b["total"] for b in buys)
total_sold  = sum(s["total"] for s in sells)
net_inv     = total_spent - total_sold
dep_val = (sum(b["total"]*(1+b["rate"]/100*(TODAY_D-date.fromisoformat(b["date"])).days/365) for b in buys)
         - sum(s["total"]*(1+s["rate"]/100*(TODAY_D-date.fromisoformat(s["date"])).days/365) for s in sells))
curr_val = tp.get("total_current", 0)
diff = curr_val - dep_val

dc = {
    "date": TODAY, "total_spent": round(total_spent,2),
    "total_sold": round(total_sold,2), "net_invested": round(net_inv,2),
    "deposit_value": round(dep_val,2), "deposit_income": round(dep_val-net_inv,2),
    "stocks_current": round(curr_val,2), "diff": round(diff,2),
    "sber_rate_today": get_rate(TODAY_D),
}

# История для графика
history = []
log_dir = BASE_DIR / "logs" / "collector"
for log_path in sorted(log_dir.glob("*.json")):
    try:
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
        sc = log.get("tinkoff_portfolio",{}).get("total_current", 0)
        log_dc = log.get("deposit_comparison", {})
        if sc and log_dc.get("deposit_value"):
            history.append({
                "date": log_path.stem, "stocks": int(sc),
                "deposit": int(log_dc["deposit_value"]),
                "invested": int(log_dc.get("net_invested", net_inv))
            })
    except: pass

# Строим rows для таблицы
all_ops = sorted(
    [{"type":"buy",**b} for b in buys] + [{"type":"sell",**s} for s in sells],
    key=lambda x: x["date"]
)
rows_js = []
dates_uniq = sorted(set(o["date"] for o in all_ops))
for d in dates_uniq:
    day_ops = [o for o in all_ops if o["date"] == d]
    day_net = sum(o["total"] if o["type"]=="buy" else -o["total"] for o in day_ops)
    days_held = (TODAY_D - date.fromisoformat(d)).days
    dep_inc = sum(o["total"]*(o["rate"]/100)*(days_held/365) for o in day_ops if o["type"]=="buy")
    for i, op in enumerate(day_ops):
        is_sell = op["type"] == "sell"
        rows_js.append({
            "date": d if i==0 else "", "type": op["type"],
            "ticker": op["ticker"], "qty": int(op["qty"]) if not is_sell else -int(op["qty"]),
            "price": round(op["price"],2), "total": round(op["total"],2),
            "day_net": round(abs(day_net),2) if i==0 else 0,
            "rate": op["rate"], "days": days_held if i==0 else 0,
            "dep_inc": round(dep_inc,2) if i==0 else 0, "is_first": i==0,
        })

# Читаем index.html и обновляем
index_file = BASE_DIR / "index.html"
with open(index_file, encoding="utf-8") as f:
    html = f.read()

# Обновляем _E
embedded = json.dumps({
    "screener": ld.get("screener",{}), "dividends": ld.get("dividends",{}),
    "meta": ld.get("meta",{}), "portfolio": ld.get("portfolio",{}),
    "currency": ld.get("currency",{}), "oil": ld.get("oil",{}),
    "rules_fired": ld.get("rules_fired",[]), "portfolio_signals": ld.get("portfolio_signals",{}),
    "assets": ld.get("assets",[]), "inefficiencies": ld.get("inefficiencies",{}),
    "biweekly_report": ld.get("biweekly_report"), "tinkoff_portfolio": tp,
    "news": ld.get("news",[]), "price_history": ld.get("price_history",{}),
    "deposit_comparison": dc,
    "operations": {"buys": buys, "sells": sells,
                   "dividends_by_ticker": divs, "as_of": TODAY},
}, ensure_ascii=False)

idx = html.find("const _E=")
end = html.find(";\nlet _log=", idx)
if idx > 0 and end > 0:
    html = html[:idx] + f"const _E={embedded}" + html[end:]

# Обновляем _DEP_SUMMARY
dep_sum = f'''const _DEP_SUMMARY = {{
  total_spent:  {round(total_spent,2)},
  total_sold:   {round(total_sold,2)},
  net_invested: {round(net_inv,2)},
  deposit_val:  {round(dep_val,2)},
  curr_val:     {round(curr_val,2)},
  diff:         {round(diff,2)},
  as_of:        "{TODAY}",
}};'''
old_sum = re.search(r'const _DEP_SUMMARY = \{[^;]+\};', html, re.DOTALL)
if old_sum:
    html = html[:old_sum.start()] + dep_sum + html[old_sum.end():]

# Обновляем _DEP_ROWS
old_rows = re.search(r'const _DEP_ROWS = \[.*?\];', html, re.DOTALL)
if old_rows:
    html = html[:old_rows.start()] + f"const _DEP_ROWS = {json.dumps(rows_js, ensure_ascii=False)};" + html[old_rows.end():]

# Обновляем _DEP_HISTORY
old_hist = re.search(r'const _DEP_HISTORY = \[.*?\];', html, re.DOTALL)
if old_hist:
    html = html[:old_hist.start()] + f"const _DEP_HISTORY = {json.dumps(history, ensure_ascii=False)};" + html[old_hist.end():]

# _logD
html = re.sub(r"_logD='[\d-]+'", f"_logD='{TODAY}'", html)

with open(index_file, "w", encoding="utf-8") as f:
    f.write(html)

print(f"index.html обновлён: порт={curr_val:,.0f}₽ вклад={dep_val:,.0f}₽ разница={diff:+,.0f}₽")
print(f"  _E: {len(buys)} покупок, {len(sells)} продаж, {len(history)} точек истории")
