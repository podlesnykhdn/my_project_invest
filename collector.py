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

def fetch(url, timeout=10, headers=None):
    import time
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.moex.com/",
        **(headers or {})
    }
    # Retry 3 раза с паузой
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2 + attempt * 2)
    raise last_err

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


# ─── МАЛОИЗВЕСТНЫЕ АКЦИИ С РАСТУЩИМ ОБЪЁМОМ ──────────────────────────────────

def collect_rising_interest(rules, all_items, vol_history):
    """
    Ищет малоизвестные акции где объём растёт неделя к неделе.
    Признаки малоизвестности: не в топ-20 по капитализации, цена < 1000 руб.
    Сигнал: объём сегодня > среднего объёма прошлой недели на 30%+
    """
    print("  [Rising interest] Анализ роста объёма...")

    # Топ-20 известных голубых фишек — исключаем
    blue_chips = {
        "SBER","SBERP","LKOH","GAZP","ROSN","NVTK","TATN","TATNP",
        "MGNT","YNDX","MTSS","MOEX","PLZL","GMKN","CHMF","NLMK",
        "MAGN","ALRS","SNGS","SNGSP"
    }

    # Портфельные тикеры тоже исключаем
    portfolio_tickers = {p["ticker"] for p in rules["portfolio"]["positions"]}

    # ETF и фонды — исключаем все (не акции компаний)
    etf_funds = {
        # Денежный рынок
        "AKMM","CASH","LQDT","SBMM","AMNR","TMON","RCMM",
        "VTBM","AIMM","GPMU","GPBS","SCMM","FMMM","PSMM",
        # Облигационные фонды
        "AKFB","AKMP","BCSD","OBLG","SUGB","SBGB","GPBM",
        "VTBB","SBRB","RCMB","BOND","RUSB","SBRS",
        # Индексные и акционные фонды
        "EQMX","TMOS","SBSP","TRUR","AKSP","BCSE","INFL",
        "SBMX","VTBX","RCMX","INMO","TIPO","DIVD","GOOD",
        # Товарные фонды
        "GOLD","TGLD","AKGD","VTBG","SBGD","CNYM","CNYU",
        "RCMG","SILV","SBSI",
        # Прочие ETF (содержат ETF в названии)
    }

    # Дополнительно исключаем по признаку ETF в названии
    def is_etf(item):
        name = item.get("name","").upper()
        return ("ETF" in name or "БПИ" in name or
                item["ticker"].endswith("ETF") or
                len(item["ticker"]) >= 5)

    exclude = blue_chips | portfolio_tickers | etf_funds

    week_key = _get_week_key()
    prev_week_key = _get_prev_week_key()

    result = []
    for item in all_items:
        ticker = item["ticker"]
        if ticker in exclude:
            continue
        if is_etf(item):
            continue
        if item["price"] <= 0 or item["price"] > 1000:
            continue
        if item["volume"] < 1_000_000:  # минимум 1 млн руб. оборота
            continue
        # Пропускаем акции только для квалифицированных инвесторов
        if ticker in QUAL_ONLY_TICKERS:
            continue

        # Сравниваем с прошлой неделей
        prev_vol = vol_history.get(prev_week_key, {}).get(ticker, 0)
        curr_vol = item["volume"]

        if prev_vol > 0:
            growth = (curr_vol - prev_vol) / prev_vol * 100
        elif curr_vol > 5_000_000:
            # Новая акция в радаре — сразу интересна если объём хороший
            growth = 100
        else:
            continue

        if growth < 30:  # рост объёма менее 30% — не интересно
            continue

        # Дополнительные сигналы
        signals = []
        if growth >= 200:
            signals.append("🚀 объём x3+")
        elif growth >= 100:
            signals.append("📈 объём x2")
        else:
            signals.append(f"↗️ объём +{growth:.0f}%")

        # Интерпретация паттерна: объём vs цена
        pct = item["pct"]
        if abs(pct) < 0.5 and growth >= 100:
            pattern = "accumulation"   # объём растёт, цена стоит — тихое накопление
            pattern_label = "🔵 Тихое накопление — объём растёт, цена стоит"
            pattern_hint  = "Кто-то набирает позицию не двигая цену. Может быть инсайд или стратегический покупатель. Следи за следующими днями."
        elif pct >= 3 and growth >= 100:
            pattern = "manipulation"   # объём и цена оба резко вверх — осторожно
            pattern_label = "🔴 Осторожно — цена и объём резко вверх"
            pattern_hint  = "Возможна манипуляция (pump): цена задрана на большом объёме. Риск резкого разворота при выходе крупного игрока."
        elif pct >= 1 and growth >= 100:
            pattern = "breakout"       # умеренный рост цены + объём — пробой
            pattern_label = "🟢 Пробой — объём подтверждает рост цены"
            pattern_hint  = "Здоровый сигнал: цена растёт на увеличенном объёме. Покупатели доминируют."
        elif pct < -1 and growth >= 100:
            pattern = "distribution"   # объём растёт, цена падает — распродажа
            pattern_label = "🟠 Распродажа — объём растёт, цена падает"
            pattern_hint  = "Крупный игрок выходит из позиции. Осторожно с покупкой."
        else:
            pattern = "neutral"
            pattern_label = "⚪ Нейтрально — сигнал требует подтверждения"
            pattern_hint  = "Недостаточно данных для однозначного вывода. Наблюдай динамику."

        if pct > 0:
            signals.append(f"цена +{pct:.1f}%")
        elif pct < -3:
            signals.append(f"⚠️ цена {pct:.1f}%")

        # Дивиденды
        div_info = rules.get("dividend_payers_directory_temp", {})
        pays_div = item.get("pays_dividends", False)
        if pays_div:
            signals.append("💰 дивиденды")

        result.append({
            "ticker":        ticker,
            "name":          item["name"],
            "price":         item["price"],
            "pct":           item["pct"],
            "volume":        curr_vol,
            "prev_volume":   prev_vol,
            "vol_growth":    round(growth, 1),
            "signals":       signals,
            "pattern":       pattern,
            "pattern_label": pattern_label,
            "pattern_hint":  pattern_hint,
            "score":         _score_rising(growth, item["pct"], curr_vol),
        })

    # Сортируем по score
    result.sort(key=lambda x: x["score"], reverse=True)
    current_list = result[:8]

    # Загружаем прошлый список для сравнения
    history_file = LOGS_DIR / "rising_history.json"
    prev_tickers = set()
    if history_file.exists():
        try:
            with open(history_file, encoding="utf-8") as f:
                prev_data = json.load(f)
            prev_tickers = set(prev_data.get("tickers", []))
            prev_week    = prev_data.get("week_key", "")
        except Exception:
            prev_tickers = set()
            prev_week    = ""
    else:
        prev_week = ""

    curr_tickers = {s["ticker"] for s in current_list}

    # Новые — появились впервые
    new_entries = [s for s in current_list if s["ticker"] not in prev_tickers]

    # Вылетели — были раньше, сейчас не в списке, но объём всё ещё есть
    dropped_tickers = prev_tickers - curr_tickers
    dropped_entries = []
    for item in all_items:
        if item["ticker"] in dropped_tickers and item["volume"] > 1_000_000:
            dropped_entries.append({
                "ticker":  item["ticker"],
                "name":    item["name"],
                "price":   item["price"],
                "pct":     item["pct"],
                "volume":  item["volume"],
                "reason":  "объём снизился или цена вышла за фильтр",
            })

    # Сохраняем текущий список для следующего раза
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump({
            "week_key": week_key,
            "date":     date.today().isoformat(),
            "tickers":  list(curr_tickers),
        }, f, ensure_ascii=False)

    print(f"  [Rising interest] Найдено: {len(current_list)} акций, новых: {len(new_entries)}, вылетело: {len(dropped_entries)}")

    return {
        "current":  current_list,
        "new":      new_entries,
        "dropped":  dropped_entries,
    }

def _get_week_key():
    d = date.today()
    week = d.isocalendar()[1]
    return f"{d.year}-W{week:02d}"

def _get_prev_week_key():
    from datetime import timedelta
    d = date.today() - timedelta(days=7)
    week = d.isocalendar()[1]
    return f"{d.year}-W{week:02d}"

def _score_rising(vol_growth, price_pct, volume):
    score = 0
    if vol_growth >= 200: score += 40
    elif vol_growth >= 100: score += 30
    elif vol_growth >= 50: score += 20
    else: score += 10
    if price_pct > 2: score += 20
    elif price_pct > 0: score += 10
    if volume >= 50_000_000: score += 20
    elif volume >= 10_000_000: score += 10
    return score


# ─── 4. СКРИНЕР MOEX ─────────────────────────────────────────────────────────


# ─── СКРИНЕР ЧЕРЕЗ TINKOFF API ───────────────────────────────────────────────

def collect_screener_tinkoff(tinkoff_token, portfolio_tickers=None):
    """
    Собирает данные скринера через Tinkoff Invest API.
    Получает список акций с объёмами и изменениями цен.
    """
    if not tinkoff_token:
        return None

    print("  [Screener] Загружаем данные через Tinkoff API...")
    base_url = "https://invest-public-api.tinkoff.ru/rest"
    t_headers = {
        "Authorization": f"Bearer {tinkoff_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # Получаем список акций через Tinkoff Instruments API
        req = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.InstrumentsService/Shares",
            data=json.dumps({"instrumentStatus": "INSTRUMENT_STATUS_BASE"}).encode(),
            headers=t_headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            instruments_data = json.loads(r.read())

        instruments = instruments_data.get("instruments", [])
        print(f"  [Screener] Получено {len(instruments)} инструментов")

        # Фильтруем только российские акции на MOEX
        ru_shares = [
            i for i in instruments
            if i.get("countryOfRisk") == "RU"
            and i.get("exchange") in ("MOEX", "MOEX_MORNING")
            and not i.get("forQualInvestorFlag", False)
        ]
        print(f"  [Screener] Российских акций для неквалов: {len(ru_shares)}")

        # Получаем цены через GetLastPrices
        figis = [i["figi"] for i in ru_shares[:200]]  # берём топ-200
        req2 = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.MarketDataService/GetLastPrices",
            data=json.dumps({"figi": figis}).encode(),
            headers=t_headers, method="POST"
        )
        with urllib.request.urlopen(req2, timeout=15) as r:
            prices_data = json.loads(r.read())

        def parse_quotation(q):
            if not q: return 0.0
            return int(q.get("units", 0)) + int(q.get("nano", 0)) / 1e9

        price_map = {}
        for lp in prices_data.get("lastPrices", []):
            price_map[lp["figi"]] = parse_quotation(lp.get("price"))

        # Строим список инструментов с ценами
        figi_to_ticker = {i["figi"]: i.get("ticker","") for i in ru_shares}
        figi_to_name   = {i["figi"]: i.get("name","") for i in ru_shares}

        result_items = []
        for figi in figis:
            price = price_map.get(figi, 0)
            if price <= 0:
                continue
            ticker = figi_to_ticker.get(figi, "")
            name   = figi_to_name.get(figi, ticker)
            if not ticker:
                continue
            result_items.append({
                "ticker": ticker,
                "figi":   figi,
                "name":   name,
                "price":  round(price, 2),
                "pct":    0,      # изменение за день — нет в lastPrices
                "volume": 0,
                "change": 0,
            })

        print(f"  [Screener] Инструментов с ценами: {len(result_items)}")
        return result_items

    except Exception as e:
        print(f"  [Screener Tinkoff] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


def collect_screener(rules):
    print("[4/6] Скринер MOEX...")
    screener_rules = rules["rules"]["screener"]["cheap_growth"]
    max_price = screener_rules["filters"]["price"]["max"]
    min_price = screener_rules["filters"]["price"]["min"]
    min_vol   = screener_rules["filters"]["liquidity"]["min_daily_turnover_rub"]

    try:
        # Пробуем несколько эндпоинтов MOEX
        data = None
        for url_try in [
            ("https://iss.moex.com/iss/engines/stock/markets/shares/"
             "boards/TQBR/securities.json?iss.meta=off&iss.only=marketdata,securities"),
            ("https://iss.moex.com/iss/engines/stock/markets/shares/"
             "boards/TQBR/securities.json?iss.meta=off&iss.only=marketdata,securities&limit=100"),
            ("https://iss.moex.com/iss/engines/stock/markets/shares/"
             "securities.json?iss.meta=off&iss.only=marketdata,securities&limit=50"),
        ]:
            data = safe_fetch(url_try, timeout=15)
            if data:
                print(f"  MOEX доступен через: {url_try[:60]}...")
                break
            print(f"  [WARN] недоступен: {url_try[:60]}")

        if not data:
            print("  [WARN] MOEX ISS недоступен — используем Tinkoff API для скринера")
            tinkoff_token = os.environ.get("TINKOFF_TOKEN")
            tinkoff_items = collect_screener_tinkoff(tinkoff_token)
            if tinkoff_items:
                # Формируем top_volume из Tinkoff данных (по цене)
                # Сортируем по цене убыванию как прокси объёма
                top_vol = sorted(
                    [i for i in tinkoff_items if i["price"] > 0],
                    key=lambda x: x["price"], reverse=True
                )[:10]
                cheap = [
                    i for i in tinkoff_items
                    if 0 < i["price"] <= 500
                ][:10]
                return {
                    "top_volume":     top_vol,
                    "cheap_growth":   cheap,
                    "rising_interest":[],
                    "rising_new":     [],
                    "rising_dropped": [],
                    "ipo":            [],
                    "_source":        "tinkoff",
                }
            return {"top_volume": [], "cheap_growth": [], "ipo": [],
                    "rising_interest": [], "rising_new": [], "rising_dropped": []}

        d = json.loads(data)
        mc = d["marketdata"]["columns"]
        sc = d["securities"]["columns"]
        md_rows = d["marketdata"]["data"]
        sc_rows = d["securities"]["data"]
        print(f"  [Screener] MOEX: {len(md_rows)} marketdata, {len(sc_rows)} securities")
        # Первые 5 строк для диагностики
        for row in md_rows[:5]:
            r = dict(zip(mc, row))
            print(f"    {r.get('SECID')}: LAST={r.get('LAST')} PREV={r.get('PREVPRICE')} VAL={r.get('VALTODAY')}")

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
        # top_vol: если объём есть — по объёму, иначе по изменению цены
        items_with_vol = [i for i in items if i["volume"] > 0]
        if items_with_vol:
            top_vol = sorted(items_with_vol, key=lambda x: x["volume"], reverse=True)[:10]
            print(f"  [Screener] top_vol по объёму: {len(top_vol)} акций")
        else:
            # Объёмы ещё не накоплены — берём по абсолютному изменению цены
            top_vol = sorted(
                [i for i in items if i.get("price", 0) > 0],
                key=lambda x: abs(x.get("pct", 0)), reverse=True
            )[:10]
            print(f"  [Screener] top_vol по изменению (объёмы=0): {len(top_vol)} акций")

        # Помечаем акции только для квалов
        for s in top_vol:
            if s.get("ticker") in QUAL_ONLY_TICKERS:
                s["qual_only"] = True
                s["signals"] = s.get("signals", []) + ["⚠️ только для квалифицированных инвесторов"]

        # Интерпретация паттерна объём/цена для топа
        for s in top_vol:
            pct = s.get("pct", 0)
            if abs(pct) < 0.3:
                s["vol_pattern"] = "neutral"
                s["vol_label"]   = "⚪ Борьба — цена стоит"
                s["vol_hint"]    = "Высокий объём без движения цены. Крупные игроки компенсируют друг друга. Жди пробоя."
            elif pct >= 2:
                s["vol_pattern"] = "buyers"
                s["vol_label"]   = "🟢 Покупатели доминируют"
                s["vol_hint"]    = f"Цена +{pct:.1f}% на высоком объёме — спрос превышает предложение."
            elif pct > 0.3:
                s["vol_pattern"] = "buyers_weak"
                s["vol_label"]   = "🟡 Слабый перевес покупателей"
                s["vol_hint"]    = f"Цена +{pct:.1f}% — покупатели чуть активнее."
            elif pct <= -2:
                s["vol_pattern"] = "sellers"
                s["vol_label"]   = "🔴 Продавцы доминируют"
                s["vol_hint"]    = f"Цена {pct:.1f}% на высоком объёме — давление продавцов."
            else:
                s["vol_pattern"] = "sellers_weak"
                s["vol_label"]   = "🟠 Слабый перевес продавцов"
                s["vol_hint"]    = f"Цена {pct:.1f}% — продавцы чуть активнее."

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

        # Сохраняем объёмы для недельной статистики
        vol_history = _load_vol_history()
        week_key = _get_week_key()
        if week_key not in vol_history:
            vol_history[week_key] = {}
        for item in items:
            if item["volume"] > 0:
                # Накапливаем объём за неделю (суммируем дни)
                prev = vol_history[week_key].get(item["ticker"], 0)
                vol_history[week_key][item["ticker"]] = prev + item["volume"]
        _save_vol_history(vol_history)

        # Малоизвестные с растущим объёмом
        rising = collect_rising_interest(rules, items, vol_history)

        # Диагностика для лога
        items_with_price = [i for i in items if i.get("price", 0) > 0]
        items_with_vol   = [i for i in items if i.get("volume", 0) > 0]
        print(f"  [Screener] items: {len(items)} всего, {len(items_with_price)} с ценой, {len(items_with_vol)} с объёмом")
        print(f"  [Screener] top_vol: {len(top_vol)}, cheap: {len(cheap_sorted)}")

        return {
            "top_volume":      top_vol,
            "cheap_growth":    cheap_sorted,
            "rising_interest": rising.get("current", rising) if isinstance(rising, dict) else rising,
            "rising_new":      rising.get("new", []) if isinstance(rising, dict) else [],
            "rising_dropped":  rising.get("dropped", []) if isinstance(rising, dict) else [],
            "_all_items":      items,
            "_debug": {
                "items_total":      len(items),
                "items_with_price": len(items_with_price),
                "items_with_vol":   len(items_with_vol),
                "top_vol_count":    len(top_vol),
                "cheap_count":      len(cheap_sorted),
                "sample":           items[:3] if items else [],
            }
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
    # Рост > 10% → штраф -15 (возможная манипуляция)
    if stock["pct"] > 10.0:
        score -= 15
    return min(max(score, 0), 100)

def _load_vol_history():
    vol_file = LOGS_DIR / "vol_history.json"
    if vol_file.exists():
        try:
            with open(vol_file, encoding="utf-8") as f:
                data = json.load(f)
            # Оставляем только последние 4 недели
            weeks = sorted(data.keys())[-4:]
            return {w: data[w] for w in weeks}
        except Exception:
            return {}
    return {}

def _save_vol_history(data):
    (LOGS_DIR).mkdir(parents=True, exist_ok=True)
    with open(LOGS_DIR / "vol_history.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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


# ─── ДИВИДЕНДНЫЙ КАЛЕНДАРЬ (ДИНАМИЧЕСКИЙ, MOEX ISS) ──────────────────────────

def fetch_dividends_moex(ticker):
    """Получаем историю дивидендов по тикеру с MOEX ISS — работает для ЛЮБОЙ акции."""
    url = f"https://iss.moex.com/iss/securities/{ticker}/dividends.json?iss.meta=off"
    data = safe_fetch(url, timeout=8)
    if not data:
        return []
    try:
        d = json.loads(data)
        cols = d["dividends"]["columns"]
        rows = d["dividends"]["data"]
        result = []
        for row in rows:
            r = dict(zip(cols, row))
            result.append({
                "registry_close_date": r.get("registryclosedate"),
                "value":   r.get("value"),
                "currency": r.get("currencyid", "RUB"),
            })
        # Сортируем по дате закрытия реестра, новые сверху
        result.sort(key=lambda x: x["registry_close_date"] or "", reverse=True)
        return result
    except Exception as e:
        print(f"    [WARN] dividends parse {ticker}: {e}")
        return []

def build_dividend_calendar(rules, screener_tickers=None):
    """
    Строит дивидендный календарь динамически через MOEX ISS:
    - для всех 5 акций портфеля
    - для всех тикеров из скринера (cheap_growth), включая новые/неизвестные
    """
    print("[7/7] Дивидендный календарь (MOEX ISS)...")
    today = date.today()
    result = {}

    portfolio_tickers = [p["ticker"] for p in rules["portfolio"]["positions"] if p["board"] == "TQBR"]
    shares_map = {p["ticker"]: p["qty"] for p in rules["portfolio"]["positions"]}

    tickers_to_check = list(portfolio_tickers)
    if screener_tickers:
        for t in screener_tickers:
            if t not in tickers_to_check:
                tickers_to_check.append(t)

    for ticker in tickers_to_check:
        history_raw = fetch_dividends_moex(ticker)
        if not history_raw:
            result[ticker] = {
                "history": [],
                "next_payment": None,
                "pays_dividends": False,
            }
            print(f"    {ticker}: нет данных по дивидендам")
            continue

        # Ищем будущие выплаты (registry_close_date >= сегодня)
        future = []
        past = []
        for h in history_raw:
            rcd = h.get("registry_close_date")
            if not rcd:
                continue
            try:
                d = datetime.strptime(rcd, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= today:
                future.append((d, h))
            else:
                past.append((d, h))

        future.sort(key=lambda x: x[0])
        past.sort(key=lambda x: x[0], reverse=True)

        shares = shares_map.get(ticker, 0)
        next_payment = None
        if future:
            record_date, h = future[0]
            amount = h.get("value", 0)
            days_to_record = (record_date - today).days
            gross = round(amount * shares, 2)
            net   = round(gross * 0.87, 2)
            next_payment = {
                "amount_per_share": amount,
                "record_date":      record_date.isoformat(),
                "days_to_record":   days_to_record,
                "your_shares":      shares,
                "your_total_gross": gross,
                "your_total_net":   net,
            }
            print(f"    {ticker}: следующая отсечка {record_date} — {amount} руб./акц.")

        history_out = []
        for d, h in past[:4]:
            history_out.append({
                "registry_close_date": d.isoformat(),
                "amount_per_share":    h.get("value"),
            })

        # Дополняем анонсированными данными которых ещё нет в MOEX ISS
        announced = rules.get("announced_dividends", {}).get("data", {}).get(ticker)
        announced_out = None
        if announced and not next_payment:
            rd = announced.get("record_date")
            days_to_record = None
            if rd:
                try:
                    rd_date = datetime.strptime(rd, "%Y-%m-%d").date()
                    days_to_record = (rd_date - today).days
                except ValueError:
                    pass
            announced_out = {**announced, "days_to_record": days_to_record}
            if "amount_per_share" in announced and shares:
                gross = round(announced["amount_per_share"] * shares, 2)
                announced_out["your_total_gross"] = gross
                announced_out["your_total_net"]   = round(gross * 0.87, 2)
                announced_out["your_shares"] = shares
            elif "amount_per_share_min" in announced and shares:
                announced_out["your_shares"] = shares
                announced_out["your_total_net_min"] = round(announced["amount_per_share_min"] * shares * 0.87, 2)
                announced_out["your_total_net_max"] = round(announced["amount_per_share_max"] * shares * 0.87, 2)

        result[ticker] = {
            "history":          history_out,
            "next_payment":     next_payment,       # факт из MOEX ISS
            "announced":        announced_out,       # анонс из внешних источников
            "pays_dividends":   len(history_raw) > 0 or announced is not None,
        }

    return result

# ─── РАСЧЁТ СТОИМОСТИ ПОРТФЕЛЯ ────────────────────────────────────────────────

def calc_portfolio(rules, quotes, tinkoff_portfolio=None):
    """
    Расчёт портфеля.
    ETF (TQTF) — цена из Tinkoff API как основной источник.
    Акции (TQBR) — цена из MOEX, fallback из Tinkoff.
    """
    # Строим карту цен из Tinkoff
    tk_prices = {}
    if tinkoff_portfolio:
        for p in tinkoff_portfolio.get("positions", []):
            t = p.get("ticker", "")
            if t and p.get("curr_price", 0) > 0:
                tk_prices[t] = p["curr_price"]

    total_value  = 0
    total_change = 0
    positions = []
    for pos in rules["portfolio"]["positions"]:
        ticker = pos["ticker"]
        board  = pos.get("board", "TQBR")
        q = quotes.get(ticker, {})

        # ETF: сначала Tinkoff, потом MOEX
        if board == "TQTF" and ticker in tk_prices:
            price  = tk_prices[ticker]
            change = 0
            pct    = 0
        else:
            price  = q.get("price", 0)
            change = q.get("change", 0)
            pct    = q.get("pct", 0)
            # Fallback из Tinkoff если MOEX вернул 0
            if price == 0 and ticker in tk_prices:
                price = tk_prices[ticker]

        value   = price * pos["qty"]
        day_rub = change * pos["qty"]
        total_value  += value
        total_change += day_rub
        # Fallback цены из Tinkoff если MOEX вернул 0
        if price == 0 and tinkoff_portfolio:
            for tp in tinkoff_portfolio.get("positions", []):
                if tp.get("ticker") == ticker and tp.get("curr_price", 0) > 0:
                    price = tp["curr_price"]
                    print(f"  {ticker}: цена из Tinkoff {price}₽ (MOEX вернул 0)")
                    break

        # P/E расчёт по данным из rules["fundamentals"]
        fund = rules.get("fundamentals", {}).get(pos["ticker"], {})
        eps  = fund.get("eps_ttm")
        pe   = round(price / eps, 1) if eps and price else None
        pe_avg = fund.get("pe_sector_avg")
        if pe and pe_avg:
            pe_vs = "ниже среднего" if pe < pe_avg * 0.9 else ("выше среднего" if pe > pe_avg * 1.1 else "на уровне среднего")
        else:
            pe_vs = None

        positions.append({
            "ticker":        pos["ticker"],
            "name":          pos["name"],
            "qty":           pos["qty"],
            "price":         price,
            "change":        change,
            "pct":           pct,
            "value":         round(value, 0),
            "day_rub":       round(day_rub, 0),
            "pe":            pe,
            "eps_ttm":       eps,
            "pe_sector_avg": pe_avg,
            "pe_vs_sector":  pe_vs,
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


# ─── ДВУХНЕДЕЛЬНЫЙ СНИМОК ПОРТФЕЛЯ ───────────────────────────────────────────

def save_biweekly_snapshot(portfolio):
    """
    Сохраняет снимок портфеля раз в две недели.
    Снимок создаётся в первый рабочий день каждых двух недель.
    """
    today = date.today()
    # Номер двухнедельного периода: 1..26 (52 недели / 2)
    week_num = today.isocalendar()[1]
    period = (week_num - 1) // 2 + 1
    period_key = f"{today.year}-P{period:02d}"

    snap_dir = LOGS_DIR / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"{period_key}.json"

    # Не перезаписываем если снимок уже есть за этот период
    if snap_file.exists():
        return period_key

    snap = {
        "period":    period_key,
        "date":      today.isoformat(),
        "total_value":  portfolio["total_value"],
        "total_change": portfolio["total_change"],
        "positions": {
            p["ticker"]: {
                "price": p["price"],
                "value": p["value"],
                "qty":   p["qty"],
            }
            for p in portfolio["positions"]
        }
    }
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"  Снимок портфеля сохранён: {period_key} ({today})")
    return period_key

def load_prev_snapshot():
    """Загружает снимок предыдущего двухнедельного периода."""
    today = date.today()
    week_num = today.isocalendar()[1]
    period = (week_num - 1) // 2 + 1
    prev_period = period - 1
    if prev_period < 1:
        prev_period = 26
        year = today.year - 1
    else:
        year = today.year
    period_key = f"{year}-P{prev_period:02d}"

    snap_file = LOGS_DIR / "snapshots" / f"{period_key}.json"
    if snap_file.exists():
        with open(snap_file, encoding="utf-8") as f:
            return json.load(f)
    return None

def build_biweekly_report(portfolio):
    """
    Строит двухнедельный отчёт сравнивая текущий снимок с предыдущим.
    Возвращает None если предыдущего снимка нет.
    """
    today = date.today()
    week_num = today.isocalendar()[1]
    period = (week_num - 1) // 2 + 1

    # Отчёт только в начале нового периода (первые 2 дня периода)
    period_start_week = (period - 1) * 2 + 1
    if week_num not in [period_start_week, period_start_week + 1]:
        return None

    prev = load_prev_snapshot()
    if not prev:
        return None

    curr_value = portfolio["total_value"]
    prev_value = prev["total_value"]
    diff_rub   = round(curr_value - prev_value, 0)
    diff_pct   = round((curr_value - prev_value) / prev_value * 100, 2) if prev_value else 0

    positions_diff = []
    for pos in portfolio["positions"]:
        t = pos["ticker"]
        prev_pos = prev["positions"].get(t, {})
        prev_price = prev_pos.get("price", 0)
        curr_price = pos["price"]
        if prev_price and curr_price:
            p_diff = round(curr_price - prev_price, 2)
            p_pct  = round((curr_price - prev_price) / prev_price * 100, 2)
            positions_diff.append({
                "ticker":     t,
                "prev_price": prev_price,
                "curr_price": curr_price,
                "diff":       p_diff,
                "pct":        p_pct,
            })

    best  = max(positions_diff, key=lambda x: x["pct"]) if positions_diff else None
    worst = min(positions_diff, key=lambda x: x["pct"]) if positions_diff else None

    return {
        "period":        f"{prev['period']} → текущий",
        "prev_date":     prev["date"],
        "curr_date":     today.isoformat(),
        "prev_value":    prev_value,
        "curr_value":    curr_value,
        "diff_rub":      diff_rub,
        "diff_pct":      diff_pct,
        "positions":     positions_diff,
        "best":          best,
        "worst":         worst,
    }



# ─── ИСТОРИЧЕСКИЕ МАКСИМУМЫ ───────────────────────────────────────────────────

def fetch_all_time_high(ticker, board="TQBR"):
    """
    Получает исторический максимум цены акции через MOEX ISS.
    Запрашивает месячные свечи за всё время и берёт максимум HIGH.
    """
    # MOEX ISS: свечи по инструменту, interval=31 (месячные), till=сегодня
    url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
           f"boards/{board}/securities/{ticker}/candles.json"
           f"?interval=31&iss.meta=off&iss.only=candles&start=0")
    data = safe_fetch(url, timeout=10)
    if not data:
        return None
    try:
        d = json.loads(data)
        cols = d["candles"]["columns"]
        rows = d["candles"]["data"]
        if not rows:
            return None
        hi_idx = cols.index("high") if "high" in cols else None
        if hi_idx is None:
            return None
        max_price = max(row[hi_idx] for row in rows if row[hi_idx])
        return round(max_price, 2)
    except Exception as e:
        print(f"    [WARN] ATH {ticker}: {e}")
        return None

def load_ath_cache():
    """Кэш исторических максимумов — обновляем раз в неделю."""
    cache_file = LOGS_DIR / "ath_cache.json"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                cache = json.load(f)
            # Проверяем возраст кэша — обновляем раз в неделю
            cache_date = cache.get("_updated", "")
            today = date.today().isoformat()
            if cache_date >= str(date.today() - __import__("datetime").timedelta(days=7)):
                return cache
        except Exception:
            pass
    return {"_updated": ""}

def save_ath_cache(cache):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cache["_updated"] = date.today().isoformat()
    with open(LOGS_DIR / "ath_cache.json", "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def collect_all_time_highs(rules):
    """Собирает исторические максимумы для всех акций портфеля."""
    print("[ATH] Исторические максимумы...")
    cache = load_ath_cache()
    result = {}

    for pos in rules["portfolio"]["positions"]:
        ticker = pos["ticker"]
        board  = pos.get("board", "TQBR")
        if board == "TQTF":
            # Для ETF ATH менее значим — пропускаем
            result[ticker] = None
            continue

        # Используем кэш если есть
        if ticker in cache and cache[ticker]:
            result[ticker] = cache[ticker]
            print(f"  {ticker}: ATH={cache[ticker]} (кэш)")
            continue

        ath = fetch_all_time_high(ticker, board)
        result[ticker] = ath
        if ath:
            cache[ticker] = ath
            print(f"  {ticker}: ATH={ath}")
        else:
            print(f"  {ticker}: ATH не получен")

    save_ath_cache(cache)
    return result



# ─── АНАЛИТИК НЕЭФФЕКТИВНОСТЕЙ ───────────────────────────────────────────────

def analyze_inefficiencies(rules, quotes, screener, vol_history):
    """
    Ищет рыночные неэффективности 5 типов:
    1. Паника/перепроданность — резкое падение без фундаментальных причин
    2. Дивидендный гэп без восстановления
    3. Объёмная аномалия — объём вырос в 5-10 раз
    4. Отрыв от отраслевого тренда
    5. Цена у многолетнего минимума + растущий объём
    """
    print("[Аналитик] Поиск неэффективностей...")
    signals = []
    portfolio_signals = []

    portfolio_tickers = {p["ticker"] for p in rules["portfolio"]["positions"]}
    all_items = screener.get("_all_items", [])

    # Загружаем предыдущий лог для сравнения
    prev_log = _load_last_log("collector")
    prev_quotes = prev_log.get("quotes", {}) if prev_log else {}
    prev_screener = prev_log.get("screener", {}) if prev_log else {}
    prev_items_map = {i["ticker"]: i for i in prev_screener.get("_all_items", [])}
    div_calendar = rules.get("dividend_calendar", {})

    week_key      = _get_week_key()
    prev_week_key = _get_prev_week_key()
    curr_vols = vol_history.get(week_key, {})
    prev_vols = vol_history.get(prev_week_key, {})

    checked = set()

    for item in all_items:
        t     = item["ticker"]
        price = item["price"]
        pct   = item["pct"]
        vol   = item["volume"]
        if not price or t in checked:
            continue
        # Пропускаем акции только для квалифицированных инвесторов
        if t in QUAL_ONLY_TICKERS:
            continue
        checked.add(t)

        in_portfolio = t in portfolio_tickers
        prev_item    = prev_items_map.get(t, {})
        prev_price   = prev_item.get("price", 0)
        prev_vol_w   = prev_vols.get(t, 0)
        curr_vol_w   = curr_vols.get(t, 0)

        found = []

        # ── ТИП 1: Паника / перепроданность ──────────────────────────────────
        # Падение > 8% за день при объёме выше среднего
        if pct <= -8 and vol >= 10_000_000:
            found.append({
                "type":    "PANIC_OVERSOLD",
                "emoji":   "😱",
                "title":   "Паника / перепроданность",
                "detail":  f"Падение {pct:.1f}% за день при высоком объёме {_fmt_vol(vol)}",
                "signal":  "Рынок реагирует эмоционально. Возможная точка входа если фундаментал не изменился.",
                "strength": min(abs(pct) * 5, 100),
            })

        # ── ТИП 2: Дивидендный гэп без восстановления ────────────────────────
        if t in div_calendar:
            div_info = div_calendar[t]
            if isinstance(div_info, dict):
                history = div_info.get("history", [])
                if history:
                    last_record = history[0].get("registry_close_date", "")
                    last_amount = history[0].get("amount_per_share", 0)
                    if last_record and last_amount:
                        from datetime import date as d_
                        try:
                            rec_d = datetime.strptime(last_record, "%Y-%m-%d").date()
                            days_since = (d_.today() - rec_d).days
                            # Гэп был 10-45 дней назад и цена ниже чем была до гэпа
                            if 10 <= days_since <= 45 and prev_price:
                                expected_recovery = prev_price - last_amount
                                if price < expected_recovery * 0.98:
                                    found.append({
                                        "type":    "DIV_GAP_NO_RECOVERY",
                                        "emoji":   "📉",
                                        "title":   "Дивидендный гэп без восстановления",
                                        "detail":  f"Отсечка {last_record} ({days_since} дн. назад), гэп {last_amount}₽ не закрыт",
                                        "signal":  "Акция не восстановилась после дивидендного гэпа. Исторически закрывается за 1-3 месяца.",
                                        "strength": 65,
                                    })
                        except Exception:
                            pass

        # ── ТИП 3: Объёмная аномалия ─────────────────────────────────────────
        if prev_vol_w > 0 and curr_vol_w > 0:
            vol_ratio = curr_vol_w / prev_vol_w
            if vol_ratio >= 4:
                found.append({
                    "type":    "VOLUME_ANOMALY",
                    "emoji":   "🔊",
                    "title":   "Объёмная аномалия",
                    "detail":  f"Объём вырос в {vol_ratio:.1f}x к прошлой неделе ({_fmt_vol(curr_vol_w)} vs {_fmt_vol(prev_vol_w)})",
                    "signal":  "Крупный игрок системно набирает или закрывает позицию. Следи за направлением цены.",
                    "strength": min(int(vol_ratio * 15), 100),
                })

        # ── ТИП 4: Отрыв от отраслевого тренда ──────────────────────────────
        # Считаем средний % изменения рынка (топ по объёму)
        top_items = screener.get("top_volume", [])
        if len(top_items) >= 5:
            market_avg = sum(i["pct"] for i in top_items[:10]) / len(top_items[:10])
            # Акция значимо отстаёт от рынка (рынок растёт, акция падает)
            divergence = pct - market_avg
            if market_avg > 1.0 and divergence < -5:
                found.append({
                    "type":    "SECTOR_DIVERGENCE",
                    "emoji":   "↕️",
                    "title":   "Отрыв от рынка (отстаёт)",
                    "detail":  f"Рынок +{market_avg:.1f}%, акция {pct:.1f}% (отрыв {divergence:.1f}%)",
                    "signal":  "Акция необъяснимо отстаёт от рынка. Возможна временная неэффективность или скрытый негатив — проверь новости.",
                    "strength": min(abs(divergence) * 8, 90),
                })
            # Акция опережает рынок когда рынок падает
            elif market_avg < -1.0 and divergence > 5:
                found.append({
                    "type":    "SECTOR_RESILIENCE",
                    "emoji":   "💪",
                    "title":   "Устойчивость на падающем рынке",
                    "detail":  f"Рынок {market_avg:.1f}%, акция {pct:.1f}% (опережение +{divergence:.1f}%)",
                    "signal":  "Акция держится когда рынок падает — признак силы или защитного спроса.",
                    "strength": min(divergence * 8, 85),
                })

        # ── ТИП 5: Цена у минимума + растущий объём ──────────────────────────
        ath = quotes.get(t, {}).get("ath") if t in portfolio_tickers else None
        if ath and price:
            pct_from_ath = (price - ath) / ath * 100
            if pct_from_ath <= -40 and vol >= 5_000_000 and pct > 0:
                found.append({
                    "type":    "NEAR_LOW_VOLUME_GROWTH",
                    "emoji":   "🔍",
                    "title":   "У многолетнего минимума с растущим объёмом",
                    "detail":  f"Цена {pct_from_ath:.0f}% от ATH ({ath}₽), сегодня +{pct:.1f}% с объёмом {_fmt_vol(vol)}",
                    "signal":  "Классическая точка разворота по Силаеву: цена на дне, объём растёт — кто-то начинает покупать.",
                    "strength": min(abs(pct_from_ath) + vol / 1_000_000, 95),
                })

        if found:
            entry = {
                "ticker":        t,
                "name":          item.get("name", t),
                "price":         price,
                "pct":           pct,
                "volume":        vol,
                "in_portfolio":  in_portfolio,
                "signals":       found,
                "max_strength":  max(s["strength"] for s in found),
            }
            if in_portfolio:
                portfolio_signals.append(entry)
            else:
                signals.append(entry)

    # Сортируем по силе сигнала
    signals.sort(key=lambda x: x["max_strength"], reverse=True)
    portfolio_signals.sort(key=lambda x: x["max_strength"], reverse=True)

    total = len(signals) + len(portfolio_signals)
    print(f"  [Аналитик] Найдено сигналов: {total} (портфель: {len(portfolio_signals)}, рынок: {len(signals)})")

    return {
        "portfolio": portfolio_signals[:5],
        "market":    signals[:10],
        "total":     total,
        "timestamp": TODAY,
    }

def _fmt_vol(v):
    if not v: return "—"
    if v >= 1e9: return f"{v/1e9:.1f}B₽"
    if v >= 1e6: return f"{v/1e6:.0f}M₽"
    return f"{v/1e3:.0f}K₽"



# ─── ИСТОРИЯ ЦЕН (OHLCV) ────────────────────────────────────────────────────

PRICES_DIR = BASE_DIR / "logs" / "prices"

def collect_price_history(rules, quotes):
    """
    Собирает дневные свечи (open/high/low/close/volume) для каждой акции портфеля.
    Данные MOEX ISS: дневные свечи за последние 90 дней.
    Хранит в logs/prices/{ticker}.json — пополняет каждый день.
    """
    print("[История цен] Сбор OHLCV данных...")
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    result = {}

    for pos in rules["portfolio"]["positions"]:
        ticker = pos["ticker"]
        board  = pos.get("board", "TQBR")

        # Загружаем существующую историю
        price_file = PRICES_DIR / f"{ticker}.json"
        if price_file.exists():
            try:
                with open(price_file, encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = {"ticker": ticker, "board": board, "days": []}
        else:
            history = {"ticker": ticker, "board": board, "days": []}

        existing_dates = {d["date"] for d in history.get("days", [])}

        # Запрашиваем свечи с MOEX ISS (дневные, interval=24)
        try:
            url = (
                f"https://iss.moex.com/iss/engines/stock/markets/shares/"
                f"boards/{board}/securities/{ticker}/candles.json"
                f"?interval=24&start=0&iss.meta=off&iss.only=candles"
            )
            data = safe_fetch(url, timeout=10)
            if not data:
                print(f"  {ticker}: нет данных с MOEX ISS")
                result[ticker] = history
                continue

            d = json.loads(data)
            cols = d["candles"]["columns"]
            rows = d["candles"]["data"]

            new_days = 0
            for row in rows:
                r = dict(zip(cols, row))
                # MOEX возвращает: open, close, high, low, value, volume, begin, end
                candle_date = (r.get("begin") or r.get("end") or "")[:10]
                if not candle_date or candle_date in existing_dates:
                    continue

                history["days"].append({
                    "date":   candle_date,
                    "open":   round(r.get("open")  or 0, 2),
                    "high":   round(r.get("high")  or 0, 2),
                    "low":    round(r.get("low")   or 0, 2),
                    "close":  round(r.get("close") or 0, 2),
                    "volume": int(r.get("volume")  or 0),
                    "value":  int(r.get("value")   or 0),
                })
                existing_dates.add(candle_date)
                new_days += 1

            # Сортируем по дате
            history["days"].sort(key=lambda x: x["date"])
            # Оставляем последние 365 дней
            history["days"] = history["days"][-365:]
            history["updated"] = today

            # Сохраняем
            with open(price_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            print(f"  {ticker}: {len(history['days'])} дней (+{new_days} новых)")

        except Exception as e:
            print(f"  {ticker}: ошибка — {e}")

        result[ticker] = {
            "ticker": ticker,
            "days_count": len(history.get("days", [])),
            "last_date": history["days"][-1]["date"] if history.get("days") else None,
            "last_close": history["days"][-1]["close"] if history.get("days") else None,
        }

    return result



# ─── Т-ИНВЕСТИЦИИ API ────────────────────────────────────────────────────────

def fetch_tinkoff_portfolio():
    """
    Получает реальные данные портфеля из Т-Инвестиций через Open API.
    Возвращает: вложено, текущая стоимость, просадка, позиции с ценами входа.
    """
    tinkoff_token = os.environ.get("TINKOFF_TOKEN")
    if not tinkoff_token:
        print("  [Tinkoff] TINKOFF_TOKEN не найден — пропускаем")
        return None

    print("  [Tinkoff] Получаем данные портфеля...")

    base_url = "https://invest-public-api.tinkoff.ru/rest"
    t_headers = {
        "Authorization": f"Bearer {tinkoff_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # Получаем список счетов
        req = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts",
            data=json.dumps({}).encode(),
            headers=t_headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            accounts_data = json.loads(r.read())

        accounts = accounts_data.get("accounts", [])
        if not accounts:
            print("  [Tinkoff] Счета не найдены")
            return None

        # Берём первый брокерский счёт
        account = next(
            (a for a in accounts if a.get("type") == "ACCOUNT_TYPE_TINKOFF"),
            accounts[0]
        )
        account_id = account["id"]
        print(f"  [Tinkoff] Счёт: {account.get('name', account_id)}")

        # Получаем портфель
        req2 = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
            data=json.dumps({"accountId": account_id, "currency": "RUB"}).encode(),
            headers=t_headers,
            method="POST"
        )
        with urllib.request.urlopen(req2, timeout=10) as r:
            portfolio_data = json.loads(r.read())

        # Справочник FIGI → тикер (обновляется автоматически)
        FIGI_TICKER_MAP = {
            "TCS03A108X38": "X5",
            "BBG0063FKTD9": "LENT",
            "RU000A101PN3":  "AKMB",
            "BBG000TY1CD1": "BELU",
            "TCS80A101X50": "TGLD",
            "BBG004730N88": "SBER",
            "RUB000UTSTOM": "RUB",
        }

        # Парсим данные
        def parse_money(m):
            if not m: return 0.0
            units = int(m.get("units", 0))
            nano  = int(m.get("nano", 0))
            return units + nano / 1e9

        total_amount_shares   = parse_money(portfolio_data.get("totalAmountShares"))
        total_amount_etf      = parse_money(portfolio_data.get("totalAmountEtf"))
        total_amount_bonds    = parse_money(portfolio_data.get("totalAmountBonds"))
        total_amount_sp       = parse_money(portfolio_data.get("totalAmountSp"))
        total_amount_portfolio = parse_money(portfolio_data.get("totalAmountPortfolio"))
        expected_yield        = parse_money(portfolio_data.get("expectedYield"))

        positions = []
        for pos in portfolio_data.get("positions", []):
            qty          = parse_money(pos.get("quantity"))
            avg_price    = parse_money(pos.get("averagePositionPrice"))
            curr_price   = parse_money(pos.get("currentPrice"))
            curr_nkd     = parse_money(pos.get("currentNkd"))
            exp_yield    = parse_money(pos.get("expectedYield"))
            avg_price_fifo = parse_money(pos.get("averagePositionPriceFifo"))

            invested     = avg_price * qty if avg_price and qty else 0
            current_val  = curr_price * qty if curr_price and qty else 0
            pnl          = exp_yield
            pnl_pct      = (pnl / invested * 100) if invested else 0

            figi_val  = pos.get("figi", "")
            ticker_val = FIGI_TICKER_MAP.get(figi_val, pos.get("instrumentType", ""))
            if ticker_val == "RUB":
                continue  # пропускаем остаток рублей

            positions.append({
                "figi":        figi_val,
                "ticker":      ticker_val,
                "itype":       pos.get("instrumentType", ""),
                "qty":         round(qty, 4),
                "avg_price":   round(avg_price, 4),
                "curr_price":  round(curr_price, 4),
                "invested":    round(invested, 2),
                "current_val": round(current_val, 2),
                "pnl":         round(pnl, 2),
                "pnl_pct":     round(pnl_pct, 2),
            })

        # Считаем общую сумму вложений
        total_invested = sum(p["invested"] for p in positions if p["invested"] > 0)
        total_current  = total_amount_portfolio
        total_pnl      = total_current - total_invested
        total_pnl_pct  = (total_pnl / total_invested * 100) if total_invested else 0

        result = {
            "account_id":    account_id,
            "account_name":  account.get("name", ""),
            "total_invested": round(total_invested, 2),
            "total_current":  round(total_current, 2),
            "total_pnl":      round(total_pnl, 2),
            "total_pnl_pct":  round(total_pnl_pct, 2),
            "total_shares":   round(total_amount_shares, 2),
            "total_etf":      round(total_amount_etf, 2),
            "total_bonds":    round(total_amount_bonds, 2),
            "positions":      positions,
            "as_of":          TODAY,
        }

        print(f"  [Tinkoff] Вложено: {total_invested:.0f}₽ | "
              f"Текущая стоимость: {total_current:.0f}₽ | "
              f"PnL: {total_pnl:+.0f}₽ ({total_pnl_pct:+.1f}%)")
        return result

    except Exception as e:
        print(f"  [Tinkoff] Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None



# ─── СИНХРОНИЗАЦИЯ ПОРТФЕЛЯ ИЗ Т-ИНВЕСТИЦИЙ ─────────────────────────────────

# Справочник FIGI → тикер (обновляй при добавлении новых позиций)
FIGI_TO_TICKER = {
    "TCS03A108X38": "X5",
    "BBG0063FKTD9": "LENT",
    "RU000A101PN3": "AKMB",
    "BBG000TY1CD1": "BELU",
    "TCS80A101X50": "TGLD",
    "BBG004730N88": "SBER",
    "RUB000UTSTOM": "RUB",
}

def sync_portfolio_from_tinkoff(rules, tinkoff_portfolio):
    """
    Обновляет qty в rules["portfolio"]["positions"] из реальных данных Т-Инвестиций.
    Добавляет новые позиции, убирает проданные.
    """
    if not tinkoff_portfolio:
        return rules

    tp_positions = tinkoff_portfolio.get("positions", [])
    if not tp_positions:
        return rules

    print("  [Sync] Синхронизация портфеля из Т-Инвестиций...")

    # Строим актуальный список из Tinkoff
    tinkoff_map = {}
    for p in tp_positions:
        ticker = p.get("ticker", "")
        if ticker in ("RUB", ""):
            continue
        qty = p.get("qty", 0)
        if qty > 0:
            tinkoff_map[ticker] = {
                "qty":       int(qty) if qty == int(qty) else qty,
                "avg_price": p.get("avg_price", 0),
            }

    if not tinkoff_map:
        return rules

    # Текущий список позиций в rules
    current_positions = rules.get("portfolio", {}).get("positions", [])
    current_map = {p["ticker"]: p for p in current_positions}

    # Обновляем qty для существующих позиций
    updated = []
    for pos in current_positions:
        t = pos["ticker"]
        if t in tinkoff_map:
            old_qty = pos.get("qty", 0)
            new_qty = tinkoff_map[t]["qty"]
            if old_qty != new_qty:
                print(f"    {t}: qty {old_qty} → {new_qty}")
                pos["qty"] = new_qty
            updated.append(pos)
        else:
            # Позиция закрыта — убираем
            print(f"    {t}: позиция закрыта, убираем")

    # Добавляем новые позиции которых нет в rules
    existing_tickers = {p["ticker"] for p in updated}
    for ticker, info in tinkoff_map.items():
        if ticker not in existing_tickers and ticker not in current_map:
            print(f"    {ticker}: новая позиция, qty={info['qty']}")
            updated.append({
                "ticker":     ticker,
                "name":       ticker,
                "short_name": ticker,
                "qty":        info["qty"],
                "board":      "TQBR",
                "div":        False,
            })

    rules["portfolio"]["positions"] = updated
    print(f"  [Sync] Позиций в портфеле: {len(updated)}")
    return rules


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

    tinkoff_portfolio = fetch_tinkoff_portfolio()
    # Синхронизируем портфель из Т-Инвестиций и сохраняем rules.json
    if tinkoff_portfolio:
        rules = sync_portfolio_from_tinkoff(rules, tinkoff_portfolio)
        try:
            rules_path = BASE_DIR / "rules.json"
            with open(rules_path, "w", encoding="utf-8") as f:
                json.dump(rules, f, ensure_ascii=False, indent=2)
            print("  [Sync] rules.json обновлён")
        except Exception as e:
            print(f"  [Sync] Ошибка сохранения rules.json: {e}")
    fired_rules, portfolio_signals = run_rules(rules, currency, oil, quotes, news)
    portfolio = calc_portfolio(rules, quotes)
    all_time_highs = collect_all_time_highs(rules)
    # Добавляем ATH и предыдущую цену в каждую позицию
    last_log = _load_last_log("collector")
    prev_quotes = last_log.get("quotes", {}) if last_log else {}
    for pos in portfolio["positions"]:
        t = pos["ticker"]
        pos["ath"] = all_time_highs.get(t)
        pos["prev_price"] = prev_quotes.get(t, {}).get("price")
        if pos["ath"] and pos["price"]:
            pct_from_ath = round((pos["price"] - pos["ath"]) / pos["ath"] * 100, 1)
            pos["pct_from_ath"] = pct_from_ath
            pos["near_ath"] = pct_from_ath >= -5  # в пределах 5% от максимума
        else:
            pos["pct_from_ath"] = None
            pos["near_ath"] = False
    save_biweekly_snapshot(portfolio)
    biweekly_report = build_biweekly_report(portfolio)

    # Собираем тикеры из скринера для проверки их дивидендов
    screener_tickers = [s["ticker"] for s in screener.get("cheap_growth", [])]
    dividends = build_dividend_calendar(rules, screener_tickers)
    vol_history_data = _load_vol_history()
    inefficiencies = analyze_inefficiencies(rules, quotes, screener, vol_history_data)
    price_history = collect_price_history(rules, quotes)

    # Применяем дивидендные данные к карточкам скринера
    for stock in screener.get("cheap_growth", []):
        div_info = dividends.get(stock["ticker"], {})
        stock["pays_dividends"] = div_info.get("pays_dividends", False)
        stock["dividend_next"]  = div_info.get("next_payment")
        if stock["pays_dividends"]:
            stock["score"] = min(stock["score"] + 10, 100)
            stock["grade"] = _grade(stock["score"])

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
        "biweekly_report":  biweekly_report,
        "inefficiencies":   inefficiencies,
        "price_history":    price_history,
        "tinkoff_portfolio": tinkoff_portfolio,
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
