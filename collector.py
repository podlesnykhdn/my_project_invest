"""
collector.py — Сборщик данных и движок правил
Читает rules.json, собирает данные из открытых источников,
прогоняет через правила, возвращает структурированный результат.
"""

import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, date
from pathlib import Path

# ─── КОНФИГ ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
RULES_FILE = BASE_DIR / "rules.json"
LOGS_DIR = BASE_DIR / "logs"
TODAY = date.today().isoformat()
NOW = datetime.now().strftime("%H:%M")
MONTH = datetime.now().month
WEEKDAY = datetime.now().weekday()  # 0=пн, 4=пт

# ─── ЗАГРУЗКА ПРАВИЛ ──────────────────────────────────────────────────────────

def load_rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        return json.load(f)

# ─── УТИЛИТЫ ──────────────────────────────────────────────────────────────────

def fetch(url, timeout=8, headers=None):
    h = {"User-Agent": "Mozilla/5.0", **(headers or {})}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def safe_fetch(url, timeout=8, headers=None):
    try:
        return fetch(url, timeout, headers)
    except Exception as e:
        print(f"  [WARN] fetch failed {url[:60]}: {e}")
        return None

# ─── 1. КУРСЫ ВАЛЮТ (ЦБ РФ) ──────────────────────────────────────────────────

def collect_currency():
    print("[1/6] Курсы валют ЦБ РФ...")
    result = {"usd": None, "eur": None, "cny": None, "usd_prev": None}
    try:
        data = fetch("https://www.cbr.ru/scripts/XML_daily.asp")
        root = ET.fromstring(data)
        for valute in root.findall("Valute"):
            code = valute.find("CharCode").text
            value = float(valute.find("Value").text.replace(",", "."))
            nominal = int(valute.find("Nominal").text)
            rate = value / nominal
            if code == "USD":
                result["usd"] = round(rate, 2)
            elif code == "EUR":
                result["eur"] = round(rate, 2)
            elif code == "CNY":
                result["cny"] = round(rate, 4)
        print(f"  USD={result['usd']} EUR={result['eur']} CNY={result['cny']}")

        # Вчерашний курс для расчёта изменения
        yesterday = datetime.now().strftime("%d/%m/%Y")
        data2 = safe_fetch(f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={yesterday}")
        if data2:
            root2 = ET.fromstring(data2)
            for valute in root2.findall("Valute"):
                if valute.find("CharCode").text == "USD":
                    result["usd_prev"] = round(
                        float(valute.find("Value").text.replace(",", ".")) /
                        int(valute.find("Nominal").text), 2
                    )
    except Exception as e:
        print(f"  [ERROR] currency: {e}")
        # Пробуем кэш
        last_log = _load_last_log("collector")
        if last_log and last_log.get("currency", {}).get("usd"):
            cached = last_log["currency"]
            cached_date = last_log.get("meta", {}).get("date", "неизвестно")
            result.update({
                "usd": cached.get("usd"),
                "eur": cached.get("eur"),
                "cny": cached.get("cny"),
                "source": f"КЭШ от {cached_date} ⚠️",
                "is_cached": True,
                "cache_date": cached_date,
            })
            print(f"  ⚠️  ВНИМАНИЕ: Курсы валют из кэша ({cached_date})!")
            print(f"  ⚠️  ЦБ РФ недоступен — данные могут быть устаревшими!")
        else:
            result["source"] = "недоступен ❌"
    return result

# ─── 2. НЕФТЬ BRENT (RSS) ─────────────────────────────────────────────────────

def collect_oil():
    print("[2/7] Нефть Brent...")
    result = {"price": None, "change": None, "change_pct": None, "source": None}
    import re

    # Источник 1: oilprice.com RSS
    try:
        data = safe_fetch("https://www.oilprice.com/rss/main", timeout=6)
        if data:
            root = ET.fromstring(data)
            for item in root.findall(".//item"):
                title = (item.find("title").text or "").lower()
                if "brent" in title:
                    prices = re.findall(r'\$?([\d.]+)', title)
                    if prices:
                        result["price"] = float(prices[0])
                        result["source"] = "oilprice.com"
                        print(f"  Brent ${result['price']} (источник: oilprice.com)")
                        return result
    except Exception as e:
        print(f"  [WARN] oilprice.com: {e}")

    # Источник 2: investing.com RSS
    try:
        data = safe_fetch("https://ru.investing.com/rss/news_25.rss", timeout=6)
        if data:
            root = ET.fromstring(data)
            for item in root.findall(".//item"):
                title = (item.find("title").text or "").lower()
                if "brent" in title or "брент" in title:
                    prices = re.findall(r'[\d.]+', title)
                    if prices:
                        result["price"] = float(prices[0])
                        result["source"] = "investing.com"
                        print(f"  Brent ${result['price']} (источник: investing.com)")
                        return result
    except Exception as e:
        print(f"  [WARN] investing.com: {e}")

    # Источник 3: MOEX фьючерс BR- (самый надёжный резерв)
    try:
        url = ("https://iss.moex.com/iss/engines/futures/markets/forts/"
               "securities/BRU5.json?iss.meta=off&iss.only=marketdata")
        data = safe_fetch(url, timeout=8)
        if data:
            d = json.loads(data)
            cols = d["marketdata"]["columns"]
            rows = d["marketdata"]["data"]
            if rows:
                r = dict(zip(cols, rows[0]))
                price = r.get("LAST") or r.get("SETTLEPRICE") or 0
                if price:
                    result["price"] = round(float(price), 2)
                    result["source"] = "MOEX фьючерс BR-"
                    print(f"  Brent ${result['price']} (источник: MOEX фьючерс BR-)")
                    return result
    except Exception as e:
        print(f"  [WARN] MOEX BR-: {e}")

    # Источник 4: кэш из последнего лога — с явным предупреждением
    last_log = _load_last_log("collector")
    if last_log and last_log.get("oil", {}).get("price"):
        cached_price = last_log["oil"]["price"]
        cached_date  = last_log.get("meta", {}).get("date", "неизвестно")
        result["price"]  = cached_price
        result["source"] = f"КЭШ от {cached_date} ⚠️"
        result["is_cached"] = True
        result["cache_date"] = cached_date
        print(f"  ⚠️  ВНИМАНИЕ: Brent из кэша ({cached_date}): ${cached_price}")
        print(f"  ⚠️  Все 3 источника недоступны — данные могут быть устаревшими!")
    else:
        result["source"] = "недоступен ❌"
        result["is_cached"] = False
        print(f"  ❌ Цена нефти недоступна — все источники не отвечают")

    return result

# ─── 3. КОТИРОВКИ MOEX ────────────────────────────────────────────────────────

def collect_moex(rules):
    print("[3/6] Котировки MOEX...")
    portfolio = rules["portfolio"]["positions"]
    quotes = {}

    # Акции (TQBR)
    share_tickers = [p["ticker"] for p in portfolio if p["board"] == "TQBR"]
    etf_tickers   = [p["ticker"] for p in portfolio if p["board"] == "TQTF"]

    def parse_board(board, tickers):
        url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
               f"boards/{board}/securities.json"
               f"?securities={','.join(tickers)}&iss.meta=off&iss.only=marketdata")
        data = safe_fetch(url)
        if not data:
            return
        d = json.loads(data)
        cols = d["marketdata"]["columns"]
        for row in d["marketdata"]["data"]:
            r = dict(zip(cols, row))
            price = r.get("LAST") or r.get("PREVPRICE") or 0
            prev  = r.get("PREVPRICE") or price
            # Используем CHANGE напрямую из MOEX — точнее чем вычислять
            change = r.get("CHANGE") or 0
            # LASTTOPREVPRICE на MOEX уже задано в процентах
            ltp = r.get("LASTTOPREVPRICE")
            if ltp is not None:
                pct = round(ltp, 2)
            elif prev:
                pct = round(change / prev * 100, 2)
            else:
                pct = 0
            if price:
                quotes[r["SECID"]] = {
                    "price":  round(price, 2),
                    "prev":   round(prev, 2),
                    "change": round(change, 2),
                    "pct":    pct,
                    "volume": r.get("VALTODAY") or 0,
                }
                print(f"  {r['SECID']}: {price} руб. ({pct:+.1f}%)")

    print(f"  Запрос акций TQBR: {share_tickers}")
    parse_board("TQBR", share_tickers)
    print(f"  Запрос ETF TQTF: {etf_tickers}")
    parse_board("TQTF", etf_tickers)
    if "TGLD" not in quotes:
        print("  [WARN] TGLD не получен с TQTF — проверка борда")
    return quotes

# ─── 4. СКРИНЕР MOEX ─────────────────────────────────────────────────────────

def collect_screener(rules):
    print("[4/6] Скринер MOEX...")
    screener_rules = rules["rules"]["screener"]["cheap_growth"]
    max_price = screener_rules["filters"]["price"]["max"]
    min_price = screener_rules["filters"]["price"]["min"]
    min_vol   = screener_rules["filters"]["liquidity"]["min_daily_turnover_rub"]

    try:
        url = ("https://iss.moex.com/iss/engines/stock/markets/shares/"
               "boards/TQBR/securities.json?iss.meta=off&iss.only=marketdata,securities")
        data = safe_fetch(url)
        if not data:
            return {"top_volume": [], "cheap_growth": [], "ipo": []}

        d = json.loads(data)
        mc = d["marketdata"]["columns"]
        sc = d["securities"]["columns"]

        names = {}
        for row in d["securities"]["data"]:
            r = dict(zip(sc, row))
            names[r["SECID"]] = r.get("SHORTNAME", r["SECID"])

        items = []
        for row in d["marketdata"]["data"]:
            r = dict(zip(mc, row))
            price = r.get("LAST") or r.get("PREVPRICE") or 0
            prev  = r.get("PREVPRICE") or price
            vol   = r.get("VALTODAY") or 0
            if not price:
                continue
            pct = (price - prev) / prev * 100 if prev else 0
            items.append({
                "ticker":  r["SECID"],
                "name":    names.get(r["SECID"], r["SECID"]),
                "price":   round(price, 2),
                "prev":    round(prev, 2),
                "change":  round(price - prev, 2),
                "pct":     round(pct, 2),
                "volume":  vol,
            })

        # Топ по объёму
        top_vol = sorted([i for i in items if i["volume"] > 0],
                         key=lambda x: x["volume"], reverse=True)[:10]

        # Дешёвые перспективные — базовая фильтрация
        cheap = [
            i for i in items
            if min_price <= i["price"] <= max_price
            and i["volume"] >= min_vol
            and 0.3 <= i["pct"] <= 15.0
        ]

        # Оценка по баллам
        for stock in cheap:
            stock["score"] = _score_stock(stock, rules)
            stock["grade"] = _grade(stock["score"])

        cheap_sorted = sorted(cheap, key=lambda x: x["score"], reverse=True)[:8]

        print(f"  Топ по объёму: {len(top_vol)} акций")
        print(f"  Дешёвые перспективные: {len(cheap_sorted)} акций")

        return {
            "top_volume":  top_vol,
            "cheap_growth": cheap_sorted,
        }

    except Exception as e:
        print(f"  [ERROR] screener: {e}")
        return {"top_volume": [], "cheap_growth": []}

def _score_stock(stock, rules):
    """Простая оценка по доступным данным (без истории)."""
    score = 0
    # Объём > 10 млн → +20
    if stock["volume"] >= 10_000_000:
        score += 20
    # Объём > 50 млн → ещё +15
    if stock["volume"] >= 50_000_000:
        score += 15
    # Рост > 1% → +15
    if stock["pct"] >= 1.0:
        score += 15
    # Рост > 3% → ещё +10
    if stock["pct"] >= 3.0:
        score += 10
    # Цена < 200 руб. → +10 (доступность)
    if stock["price"] < 200:
        score += 10
    # Дивидендная история 2+ лет → +10
    div_payers = rules.get("dividend_payers_directory", {}).get("tickers", [])
    if stock["ticker"] in div_payers:
        score += 10
        stock["pays_dividends"] = True
    else:
        stock["pays_dividends"] = False
    # Рост > 10% → штраф -15 (возможная манипуляция)
    if stock["pct"] > 10.0:
        score -= 15
    return min(max(score, 0), 100)

def _grade(score):
    if score >= 75: return "🟢 A"
    if score >= 50: return "🟡 B"
    if score >= 30: return "🟠 C"
    return "🔴 D"


# ─── 5. АКТИВЫ: ЗОЛОТО, НЕФТЬ, СЕРЕБРО (MOEX) ───────────────────────────────

def collect_assets(rules, oil=None):
    print("[5/7] Активы на MOEX (золото, нефть, серебро)...")
    assets_config = rules.get("watchlist_assets", {}).get("commodities", [])
    scoring_criteria = rules.get("watchlist_assets", {}).get("scoring_assets", {})
    usd_change = 0  # будет передан позже через run_rules

    result = {}

    def pct_from(r, price, prev, change):
        ltp = r.get("LASTTOPREVPRICE")
        if ltp is not None:
            return round(ltp, 2)
        if prev:
            return round(change / prev * 100, 2)
        return 0

    try:
        # Золото (TGLD) и Серебро (SILV) — борд TQTF
        etf_tickers = [a["ticker"] for a in assets_config if a["ticker"] in ("TGLD", "SILV")]
        url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
               f"boards/TQTF/securities.json"
               f"?securities={','.join(etf_tickers)}&iss.meta=off&iss.only=marketdata")
        data = safe_fetch(url)
        if data:
            d = json.loads(data)
            cols = d["marketdata"]["columns"]
            for row in d["marketdata"]["data"]:
                r = dict(zip(cols, row))
                price  = r.get("LAST") or r.get("PREVPRICE") or 0
                prev   = r.get("PREVPRICE") or price
                vol    = r.get("VALTODAY") or 0
                change = r.get("CHANGE") or 0
                if price:
                    pct = pct_from(r, price, prev, change)
                    result[r["SECID"]] = {
                        "price": round(price, 2), "prev": round(prev, 2),
                        "change": round(change, 2), "pct": pct, "volume": vol,
                        "score": 0, "grade": "🔴 D",
                    }
                    print(f"  {r['SECID']}: {price} руб. ({pct:+.1f}%)")
        else:
            print(f"  [WARN] TQTF (золото/серебро) недоступен")

        # Нефть Brent — переиспользуем уже полученную цену из collect_oil()
        # (BR- как тикер на TQTF не существует, поэтому работаем через данные нефти)
    except Exception as e:
        print(f"  [ERROR] assets: {e}")

    # Оценка перспективности по критериям (упрощённая — без истории)
    for ticker, q in result.items():
        score = 0
        reasons = []
        # Тренд сегодня
        if q["pct"] > 0:
            score += 30
            reasons.append("растёт сегодня")
        # Объём
        if q["volume"] >= 1_000_000:
            score += 25
            reasons.append("объём активный")
        # Рост > 1%
        if q["pct"] >= 1.0:
            score += 15
            reasons.append(f"рост +{q['pct']:.1f}%")
        q["score"] = min(score, 100)
        q["grade"] = _grade(q["score"])
        q["reasons"] = reasons

    # Добавляем метаданные из конфига
    assets_out = []
    for asset in assets_config:
        ticker = asset["ticker"]
        q = result.get(ticker, {})

        # Нефть Brent — берём из collect_oil(), а не из MOEX TQTF
        if ticker == "BR-" and oil and oil.get("price"):
            assets_out.append({
                "name":         asset["name"],
                "ticker":       "Brent",
                "in_portfolio": asset["in_portfolio"],
                "why_watch":    asset["why_watch"],
                "price":        oil.get("price", 0),
                "change":       0,
                "pct":          0,
                "volume":       0,
                "score":        0,
                "grade":        "ℹ️",
                "reasons":      [f"Источник: {oil.get('source','?')}"],
                "signals":      asset["signals"],
                "note":         asset.get("note", "") + " (цена в USD/баррель)",
            })
            continue

        assets_out.append({
            "name":         asset["name"],
            "ticker":       ticker,
            "in_portfolio": asset["in_portfolio"],
            "why_watch":    asset["why_watch"],
            "price":        q.get("price", 0),
            "change":       q.get("change", 0),
            "pct":          q.get("pct", 0),
            "volume":       q.get("volume", 0),
            "score":        q.get("score", 0),
            "grade":        q.get("grade", "🔴 D"),
            "reasons":      q.get("reasons", []),
            "signals":      asset["signals"],
            "note":         asset.get("note", ""),
        })

    print(f"  Активов получено: {len([a for a in assets_out if a['price'] > 0])}/{len(assets_out)}")
    return assets_out


# ─── 6. НОВОСТИ (RSS) ─────────────────────────────────────────────────────────

def collect_news(rules):
    print("[6/7] Новости RSS...")
    feeds = rules["data_sources"]["rss_feeds"]
    keywords_map = rules["rules"]["news_keywords"]
    global_events = rules["rules"]["global_events"]

    all_news = []
    for feed in feeds:
        data = safe_fetch(feed["url"], timeout=6)
        if not data:
            continue
        try:
            root = ET.fromstring(data)
            for item in root.findall(".//item")[:30]:
                title_el = item.find("title")
                link_el  = item.find("link")
                if title_el is None:
                    continue
                all_news.append({
                    "source": feed["name"],
                    "title":  (title_el.text or "").strip(),
                    "link":   (link_el.text or "").strip() if link_el is not None else "",
                })
        except Exception:
            continue

    print(f"  Собрано новостей: {len(all_news)}")

    # Сортируем по позициям портфеля
    portfolio_news = {ticker: [] for ticker in keywords_map}
    global_alerts  = []

    for news in all_news:
        text = news["title"].lower()

        # Проверка глобальных событий
        for event in global_events:
            if any(kw.lower() in text for kw in event["keywords"]):
                if not any(a["id"] == event["id"] for a in global_alerts):
                    global_alerts.append({
                        "id": event["id"],
                        "signal": event["signal"],
                        "message": event["message"],
                        "news_title": news["title"],
                        "source": news["source"],
                        "portfolio_impact": event["portfolio_impact"],
                    })

        # Проверка по тикерам
        for ticker, kw_lists in keywords_map.items():
            pos_hit = any(kw.lower() in text for kw in kw_lists.get("positive", []))
            neg_hit = any(kw.lower() in text for kw in kw_lists.get("negative", []))
            if pos_hit or neg_hit:
                if len(portfolio_news[ticker]) < 3:
                    portfolio_news[ticker].append({
                        "signal": "positive" if pos_hit else "negative",
                        "title":  news["title"],
                        "source": news["source"],
                        "link":   news["link"],
                    })

    return {
        "portfolio_news": portfolio_news,
        "global_alerts":  global_alerts,
        "total_collected": len(all_news),
    }

# ─── 6. ДВИЖОК ПРАВИЛ ─────────────────────────────────────────────────────────

def run_rules(rules, currency, oil, quotes, news):
    print("[7/7] Применяю правила...")
    fired = []
    portfolio_signals = {}

    usd_change = 0.0
    if currency.get("usd") and currency.get("usd_prev"):
        usd_change = round((currency["usd"] - currency["usd_prev"]) /
                           currency["usd_prev"] * 100, 2)

    # Правила по валюте — только если есть данные о курсе
    if currency.get("usd") is not None:
        for rule in rules["rules"]["currency"]:
            hit = False
            if "usd_change >= 2.0" in rule["condition"] and usd_change >= 2.0:
                hit = True
            elif "usd_change <= -2.0" in rule["condition"] and usd_change <= -2.0:
                hit = True
            elif "abs(usd_change) < 2.0" in rule["condition"] and abs(usd_change) < 2.0:
                hit = True
            if hit:
                fired.append({
                    "rule_id": rule["id"],
                    "signal":  rule["signal"],
                    "message": rule["message"].format(
                        usd_change=usd_change,
                        usd_rate=currency.get("usd") or 0
                    ),
                    "portfolio_impact": rule.get("portfolio_impact", {})
                })
                _merge_signals(portfolio_signals, rule.get("portfolio_impact", {}))
    else:
        fired.append({
            "rule_id": "CURRENCY_UNAVAILABLE",
            "signal":  "warning",
            "message": "⚠️ Курсы валют недоступны (ЦБ РФ не отвечает) — данные могут быть устаревшими",
            "portfolio_impact": {}
        })

    # Правила по нефти
    if oil.get("change_pct"):
        for rule in rules["rules"]["oil"]:
            hit = False
            if "oil_change <= -3.0" in rule["condition"] and oil["change_pct"] <= -3.0:
                hit = True
            elif "oil_change >= 3.0" in rule["condition"] and oil["change_pct"] >= 3.0:
                hit = True
            if hit:
                fired.append({
                    "rule_id": rule["id"],
                    "signal":  rule["signal"],
                    "message": rule["message"].format(oil_change=oil["change_pct"]),
                    "portfolio_impact": rule.get("portfolio_impact", {})
                })
                _merge_signals(portfolio_signals, rule.get("portfolio_impact", {}))

    # Сезонные факторы
    if MONTH in [5, 6, 7]:
        fired.append({
            "rule_id": "DIV_SEASON_ACTIVE",
            "signal":  "info",
            "message": "Дивидендный сезон — следи за датами отсечек по X5, Сберу и Novabev",
            "portfolio_impact": {}
        })

    if MONTH in [11, 12, 1]:
        fired.append({
            "rule_id": "NEW_YEAR_SEASON",
            "signal":  "positive",
            "message": "Новогодний сезон — традиционный пик продаж для Novabev и ритейла",
            "portfolio_impact": {
                "BELU": {"signal": "positive", "reason": "Пик продаж алкоголя"},
                "LENT": {"signal": "positive", "reason": "Рост трафика в магазинах"},
                "X5":   {"signal": "positive", "reason": "Новогодний рост выручки"},
            }
        })

    # Глобальные новостные события
    for alert in news.get("global_alerts", []):
        fired.append({
            "rule_id": alert["id"],
            "signal":  alert["signal"],
            "message": f"{alert['message']} ({alert['source']}): {alert['news_title'][:80]}",
            "portfolio_impact": alert.get("portfolio_impact", {})
        })
        _merge_signals(portfolio_signals, alert.get("portfolio_impact", {}))

    print(f"  Сработало правил: {len(fired)}")
    return fired, portfolio_signals

def _merge_signals(signals, impact):
    for ticker, data in impact.items():
        if ticker not in signals:
            signals[ticker] = []
        signals[ticker].append(data)


# ─── ДИВИДЕНДНЫЙ КАЛЕНДАРЬ ────────────────────────────────────────────────────

def build_dividend_calendar(rules):
    print("[7/7] Дивидендный календарь...")
    calendar = rules.get("dividend_calendar", {})
    today = date.today()
    result = {}

    META_KEYS = {"description", "updated", "source"}
    for ticker, info in calendar.items():
        if ticker in META_KEYS or not isinstance(info, dict):
            continue
        next_pay = info.get("next_payment", {})
        entry = {
            "name":    info.get("name", ticker),
            "history": info.get("history", []),
            "status":  next_pay.get("status", ""),
        }

        # Считаем дни до даты отсечки/выплаты если есть
        record_date  = next_pay.get("record_date")
        payment_date = next_pay.get("payment_date")

        days_to_record  = None
        days_to_payment = None
        if record_date:
            try:
                rd = datetime.strptime(record_date, "%Y-%m-%d").date()
                days_to_record = (rd - today).days
            except ValueError:
                pass
        if payment_date:
            try:
                pd_ = datetime.strptime(payment_date, "%Y-%m-%d").date()
                days_to_payment = (pd_ - today).days
            except ValueError:
                pass

        entry["record_date"]     = record_date
        entry["payment_date"]    = payment_date
        entry["days_to_record"]  = days_to_record
        entry["days_to_payment"] = days_to_payment

        # Сумма дивиденда и доход
        if "amount_per_share" in next_pay:
            entry["amount_per_share"] = next_pay["amount_per_share"]
            entry["your_total_gross"] = next_pay.get("your_total_gross")
            entry["your_total_net"]   = next_pay.get("your_total_net")
        elif "amount_per_share_min" in next_pay:
            entry["amount_per_share_min"] = next_pay["amount_per_share_min"]
            entry["amount_per_share_max"] = next_pay["amount_per_share_max"]
            entry["your_total_net_min"]   = next_pay.get("your_total_net_min")
            entry["your_total_net_max"]   = next_pay.get("your_total_net_max")

        entry["your_shares"] = next_pay.get("your_shares", 0)

        result[ticker] = entry

    return result

# ─── РАСЧЁТ СТОИМОСТИ ПОРТФЕЛЯ ────────────────────────────────────────────────

def calc_portfolio(rules, quotes):
    total_value  = 0
    total_change = 0
    positions = []
    for pos in rules["portfolio"]["positions"]:
        q = quotes.get(pos["ticker"], {})
        price  = q.get("price", 0)
        change = q.get("change", 0)
        pct    = q.get("pct", 0)
        value  = price * pos["qty"]
        day_rub = change * pos["qty"]
        total_value  += value
        total_change += day_rub
        positions.append({
            "ticker":    pos["ticker"],
            "name":      pos["name"],
            "qty":       pos["qty"],
            "price":     price,
            "change":    change,
            "pct":       pct,
            "value":     round(value, 0),
            "day_rub":   round(day_rub, 0),
        })
    total_pct = (total_change / (total_value - total_change) * 100
                 if total_value - total_change else 0)
    return {
        "total_value":  round(total_value, 0),
        "total_change": round(total_change, 0),
        "total_pct":    round(total_pct, 2),
        "positions":    positions,
    }

# ─── ЛОГИРОВАНИЕ ─────────────────────────────────────────────────────────────

def save_log(data):
    log_dir = LOGS_DIR / "collector"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{TODAY}.json"
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  Лог сохранён: {log_file}")

def _load_last_log(role):
    log_dir = LOGS_DIR / role
    if not log_dir.exists():
        return None
    files = sorted(log_dir.glob("*.json"))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)

# ─── ГЛАВНАЯ ФУНКЦИЯ ──────────────────────────────────────────────────────────

def collect():
    print(f"\n{'='*50}")
    print(f"Сборщик запущен: {TODAY} {NOW}")
    print(f"{'='*50}\n")

    rules = load_rules()

    currency = collect_currency()
    oil      = collect_oil()
    quotes   = collect_moex(rules)
    screener = collect_screener(rules)
    assets   = collect_assets(rules, oil)
    news     = collect_news(rules)

    fired_rules, portfolio_signals = run_rules(rules, currency, oil, quotes, news)
    portfolio = calc_portfolio(rules, quotes)
    dividends = build_dividend_calendar(rules)

    usd_change = 0.0
    if currency.get("usd") and currency.get("usd_prev"):
        usd_change = round(
            (currency["usd"] - currency["usd_prev"]) / currency["usd_prev"] * 100, 2
        )

    result = {
        "meta": {
            "date":      TODAY,
            "time":      NOW,
            "month":     MONTH,
            "weekday":   WEEKDAY,
            "is_friday": WEEKDAY == 4,
        },
        "currency": {
            **currency,
            "usd_change": usd_change,
        },
        "oil":       oil,
        "portfolio": portfolio,
        "quotes":    quotes,
        "screener":  screener,
        "assets":    assets,
        "dividends": dividends,
        "news":      news,
        "rules_fired":        fired_rules,
        "portfolio_signals":  portfolio_signals,
    }

    save_log(result)

    print(f"\n{'='*50}")
    print(f"Готово! Правил сработало: {len(fired_rules)}")
    print(f"Стоимость портфеля: {portfolio['total_value']:,.0f} руб. "
          f"({portfolio['total_change']:+,.0f} руб.)")
    print(f"{'='*50}\n")

    return result

if __name__ == "__main__":
    collect()
