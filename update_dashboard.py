#!/usr/bin/env python3
"""
update_dashboard.py — обновляет index.html актуальными данными из Tinkoff API.
Пересобирает _E, _DEP_ROWS, _DEP_SUMMARY, _DEP_HISTORY, _TGLD_HISTORY каждый день.
"""
import json, re, sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
TODAY    = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
TODAY_D  = date.fromisoformat(TODAY)

SBER_DEPOSIT_RATES = [
    (date(2024,1,1),10.0),(date(2024,9,20),12.0),(date(2024,10,29),13.5),
    (date(2025,2,22),12.0),(date(2025,6,9),10.0),(date(2025,9,1),9.0),
    (date(2025,12,20),9.0),(date(2026,1,5),8.0),(date(2026,2,20),7.5),
    (date(2026,4,1),7.0),(date(2026,4,30),6.5),
]
def get_rate(d_str):
    d = date.fromisoformat(d_str) if isinstance(d_str, str) else d_str
    rate = SBER_DEPOSIT_RATES[0][1]
    for rd, r in SBER_DEPOSIT_RATES:
        if d >= rd: rate = r
        else: break
    return rate

MY_TICKERS = {"SBER","X5","LENT","BELU","TGLD"}

def load_operations():
    ops_file = BASE_DIR / "logs" / "operations_history.json"
    if not ops_file.exists():
        print("  [update] operations_history.json не найден")
        return [], [], {}
    with open(ops_file, encoding="utf-8") as f:
        ops = json.load(f)
    buys  = [b for b in ops.get("buys",[])  if b.get("ticker") in MY_TICKERS]
    sells = [s for s in ops.get("sells",[]) if s.get("ticker") in MY_TICKERS]
    divs  = ops.get("dividends_by_ticker", {})
    for b in buys:  b["rate"] = get_rate(b["date"])
    for s in sells: s["rate"] = get_rate(s["date"])
    return buys, sells, divs

def build_dep_rows(buys, sells):
    all_ops = sorted(
        [{"type":"buy",**b} for b in buys] + [{"type":"sell",**s} for s in sells],
        key=lambda x: (x["date"], x["type"])
    )
    day_nets = defaultdict(float)
    for op in all_ops:
        day_nets[op["date"]] += op["total"] if op["type"]=="buy" else -op["total"]
    rows = []
    prev_date = None
    for op in all_ops:
        d        = op["date"]
        days     = (TODAY_D - date.fromisoformat(d)).days
        rate     = op["rate"]
        is_sell  = op["type"] == "sell"
        is_first = (d != prev_date)
        prev_date = d
        dep_full = round(op["total"] * (1 + rate/100 * days/365), 2)
        rows.append({
            "date":      d if is_first else "",
            "real_date": d,
            "type":      op["type"],
            "ticker":    op["ticker"],
            "qty":       int(op["qty"]) if not is_sell else -int(op["qty"]),
            "price":     round(op["price"], 2),
            "total":     round(op["total"], 2),
            "day_net":   round(abs(day_nets[d]), 2) if is_first else 0,
            "rate":      rate,
            "days":      days,
            "dep_full":  dep_full,
            "dep_inc":   round(dep_full - op["total"], 2),
            "is_sell":   is_sell,
            "is_first":  is_first,
        })
    return rows

def build_dep_summary(buys, sells, curr_val):
    total_spent = sum(b["total"] for b in buys)
    total_sold  = sum(s["total"] for s in sells)
    net_inv     = total_spent - total_sold
    dep_total   = (
        sum(b["total"]*(1+b["rate"]/100*(TODAY_D-date.fromisoformat(b["date"])).days/365) for b in buys)
      - sum(s["total"]*(1+s["rate"]/100*(TODAY_D-date.fromisoformat(s["date"])).days/365) for s in sells)
    )
    return {
        "total_spent": round(total_spent,2), "total_sold": round(total_sold,2),
        "net_invested": round(net_inv,2), "deposit_val": round(dep_total,2),
        "curr_val": round(curr_val,2), "diff": round(curr_val-dep_total,2),
        "deposit_income": round(dep_total-net_inv,2),
        "sber_rate_today": get_rate(TODAY),
    }

def build_dep_history():
    log_dir = BASE_DIR / "logs" / "collector"
    history = []
    for lp in sorted(log_dir.glob("*.json")):
        try:
            with open(lp, encoding="utf-8") as f: log = json.load(f)
            sc = log.get("tinkoff_portfolio",{}).get("total_current",0)
            dc = log.get("deposit_comparison",{})
            if sc and dc.get("deposit_value"):
                history.append({"date":lp.stem,"stocks":int(sc),
                    "deposit":int(dc["deposit_value"]),"invested":int(dc.get("net_invested",0))})
        except: pass
    return history

def build_tgld_history():
    log_dir = BASE_DIR / "logs" / "collector"
    history = []
    for lp in sorted(log_dir.glob("*.json")):
        try:
            with open(lp, encoding="utf-8") as f: log = json.load(f)
            tgld = next((p for p in log.get("tinkoff_portfolio",{}).get("positions",[])
                        if p.get("ticker")=="TGLD"), None)
            if not tgld: continue
            curr = log.get("currency",{})
            usd  = float(curr.get("usd",0) or curr.get("USD",0) or 0)
            price = float(tgld["curr_price"])
            if price and usd:
                history.append({"date":lp.stem,"tgld_price":round(price,2),
                    "usd_rub":round(usd,2),"tgld_rub_x_usd":round(price*usd,2)})
        except: pass
    return history

def main():
    log_file = BASE_DIR / "logs" / "collector" / f"{TODAY}.json"
    if not log_file.exists():
        print(f"  [update] Лог {TODAY} не найден"); sys.exit(0)
    with open(log_file, encoding="utf-8") as f: ld = json.load(f)
    tp       = ld.get("tinkoff_portfolio", {})
    curr_val = tp.get("total_current", 0)
    if not tp:
        print("  [update] tinkoff_portfolio пустой"); sys.exit(0)

    buys, sells, divs = load_operations()
    print(f"  [update] Операций: {len(buys)} покупок, {len(sells)} продаж")

    dep_rows    = build_dep_rows(buys, sells)
    dep_summary = build_dep_summary(buys, sells, curr_val)
    dep_history = build_dep_history()
    tgld_hist   = build_tgld_history()

    index_file = BASE_DIR / "index.html"
    with open(index_file, encoding="utf-8") as f: html = f.read()

    embedded = json.dumps({
        "screener":ld.get("screener",{}),"dividends":ld.get("dividends",{}),
        "meta":ld.get("meta",{}),"portfolio":ld.get("portfolio",{}),
        "currency":ld.get("currency",{}),"oil":ld.get("oil",{}),
        "rules_fired":ld.get("rules_fired",[]),"portfolio_signals":ld.get("portfolio_signals",{}),
        "assets":ld.get("assets",[]),"inefficiencies":ld.get("inefficiencies",{}),
        "biweekly_report":ld.get("biweekly_report"),"tinkoff_portfolio":tp,
        "news":ld.get("news",[]),"price_history":ld.get("price_history",{}),
        "deposit_comparison":{
            "date":TODAY,"total_spent":dep_summary["total_spent"],
            "total_sold":dep_summary["total_sold"],"net_invested":dep_summary["net_invested"],
            "deposit_value":dep_summary["deposit_val"],"deposit_income":dep_summary["deposit_income"],
            "stocks_current":curr_val,"diff":dep_summary["diff"],
            "sber_rate_today":dep_summary["sber_rate_today"],
        },
        "operations":{"buys":buys,"sells":sells,"dividends_by_ticker":divs,"as_of":TODAY},
    }, ensure_ascii=False)

    idx = html.find("const _E="); end = html.find(";\nlet _log=", idx)
    if idx > 0 and end > 0:
        html = html[:idx] + "const _E=" + embedded + html[end:]

    old_r = re.search(r'const _DEP_ROWS = \[.*?\];', html, re.DOTALL)
    if old_r:
        html = html[:old_r.start()] + "const _DEP_ROWS = " + json.dumps(dep_rows, ensure_ascii=False) + ";" + html[old_r.end():]

    ds = dep_summary
    old_s = re.search(r'const _DEP_SUMMARY = \{[^;]+\};', html, re.DOTALL)
    if old_s:
        new_sum = (
            "const _DEP_SUMMARY = {\n"
            "  total_spent:  " + str(ds["total_spent"]) + ",\n"
            "  total_sold:   " + str(ds["total_sold"]) + ",\n"
            "  net_invested: " + str(ds["net_invested"]) + ",\n"
            "  deposit_val:  " + str(ds["deposit_val"]) + ",\n"
            "  curr_val:     " + str(ds["curr_val"]) + ",\n"
            "  diff:         " + str(ds["diff"]) + ",\n"
            "  as_of:        \"" + TODAY + "\",\n};"
        )
        html = html[:old_s.start()] + new_sum + html[old_s.end():]

    old_h = re.search(r'const _DEP_HISTORY = \[.*?\];', html, re.DOTALL)
    if old_h:
        html = html[:old_h.start()] + "const _DEP_HISTORY = " + json.dumps(dep_history, ensure_ascii=False) + ";" + html[old_h.end():]

    old_t = re.search(r'const _TGLD_HISTORY = \[.*?\];', html, re.DOTALL)
    if old_t and tgld_hist:
        html = html[:old_t.start()] + "const _TGLD_HISTORY = " + json.dumps(tgld_hist, ensure_ascii=False) + ";" + html[old_t.end():]

    html = re.sub(r"_logD='[\d-]+'", "_logD='" + TODAY + "'", html)

    with open(index_file, "w", encoding="utf-8") as f: f.write(html)
    print(f"  [update] OK: порт={curr_val:,.0f} вклад={ds['deposit_val']:,.0f} разница={ds['diff']:+,.0f}")
    print(f"  [update] _DEP_ROWS:{len(dep_rows)} _DEP_HISTORY:{len(dep_history)} _TGLD:{len(tgld_hist)}")

if __name__ == "__main__":
    main()
