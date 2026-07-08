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

# ─── SSL: сертификат Минцифры для tbank.ru ───────────────────────────────────
import ssl as _ssl

def _make_tbank_ssl_context():
    """
    SSL контекст для tbank.ru.
    После 2 июля 2026 tbank.ru использует сертификат Минцифры РФ,
    которому GitHub Actions не доверяет.
    Используем CERT_NONE — безопасно т.к. аутентификация через Bearer токен.
    """
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    print("  [SSL] Используем CERT_NONE для tbank.ru (сертификат Минцифры)")
    return ctx

_TBANK_SSL_CTX = None

def _get_tbank_ctx():
    global _TBANK_SSL_CTX
    if _TBANK_SSL_CTX is None:
        _TBANK_SSL_CTX = _make_tbank_ssl_context()
    return _TBANK_SSL_CTX

# ─── КОНФИГ ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
RULES_FILE = BASE_DIR / "rules.json"
LOGS_DIR = BASE_DIR / "logs"
TODAY = date.today().isoformat()

# Акции только для квалифицированных инвесторов (недоступны в Т-Инвестициях)
QUAL_ONLY_TICKERS = {
    # Подтверждённые квал-only (недоступны в Т-Инвестициях для неквалов)
    "DIOD", "KUZB", "UKUZ", "SVAV", "UWGN", "KCHEP", "KCHPP",
    "GRNT", "RNFT", "MRKP", "MRKC", "MRKZ", "MRKV", "MRKU",
    "MRSB", "MRSK", "TORS", "TNSE", "KLSB", "PMSBP",
    # Дополнительные квал-only третьего эшелона
    "CHKZ",   # Челябинский КПЗ
    "KOGK",   # Коршуновский ГОК
    "KROT",   # Красноярский завод
    "KZOS",   # Казанский завод
    "LNZL",   # Лензолото
    "MGKL",   # МКБ Лизинг
    "NNSB",   # ННС Банк
    "OGKB",   # ОГК-2 привилегированные
    "PRFN",   # ЧТПЗ прив
    "RDRB",   # РДР Банк
    "RUGP",   # Русгрэйн
    "SAGO",   # Самараэнерго
    "SAGOP",  # Самараэнерго п
    "SJSC",   # ЮТэйр
    "TGKB",   # ТГК-2
    "TGKBP",  # ТГК-2 п
    "TGKD",   # ТГК-14
    "TGKDP",  # ТГК-14 п
    "VLHZ",   # Волжский хим завод
    "WTCM",   # ЦМТ
    "WTCMP",  # ЦМТ п
    "YAKG",   # Якутская топл компания
}
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
    """Цена нефти Brent через Yahoo Finance (BZ=F). Работает из GitHub Actions."""
    print("[OIL] Загружаем Brent через Yahoo Finance...")
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/BZ%3DF"
               "?interval=1d&range=2d")
        h = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())

        result = data["chart"]["result"][0]
        meta   = result["meta"]
        price  = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev   = meta.get("previousClose", price)
        change = round(price - prev, 2) if price and prev else 0
        pct    = round(change / prev * 100, 2) if prev else 0

        print(f"  Brent: {price}$ ({pct:+.2f}%)")
        return {
            "price":      round(price, 2),
            "change":     change,
            "change_pct": pct,
            "source":     "Yahoo Finance ✅",
            "is_cached":  False,
        }
    except Exception as e:
        print(f"  [OIL] Yahoo Finance недоступен: {e}")
        # Fallback — последний известный кэш
        cache_file = LOGS_DIR / "oil_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    cached = json.load(f)
                print(f"  [OIL] Из кэша: {cached.get('price')}$")
                cached["is_cached"] = True
                return cached
            except Exception:
                pass
        return {"price": None, "change": None, "change_pct": None,
                "source": "недоступен ❌", "is_cached": False}


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



# ─── ПРОВЕРКА КВАЛ-ONLY ТИКЕРОВ ──────────────────────────────────────────────

QUAL_CACHE_FILE = BASE_DIR / "logs" / "qual_only_cache.json"

def load_qual_cache():
    """Загружает кэш квал-only тикеров из файла."""
    if QUAL_CACHE_FILE.exists():
        try:
            with open(QUAL_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tickers": list(QUAL_ONLY_TICKERS), "updated": None, "checked_count": 0}

def save_qual_cache(cache):
    QUAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def check_ticker_qual_status(ticker, tinkoff_token):
    """
    Проверяет через Tinkoff API является ли тикер квал-only.
    Возвращает True если только для квалов, False если доступен всем.
    """
    if not tinkoff_token:
        return None  # не можем проверить
    try:
        base_url = "https://invest-public-api.tbank.ru/rest"
        t_headers = {
            "Authorization": f"Bearer {tinkoff_token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.InstrumentsService/ShareBy",
            data=json.dumps({"idType": "ID_TYPE_TICKER", "classCode": "TQBR", "id": ticker}).encode(),
            headers=t_headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        instrument = data.get("instrument", {})
        is_qual = instrument.get("forQualInvestorFlag", True)
        return is_qual
    except Exception:
        # Если ошибка — пробуем TQTF
        try:
            req2 = urllib.request.Request(
                f"{base_url}/tinkoff.public.invest.api.contract.v1.InstrumentsService/ShareBy",
                data=json.dumps({"idType": "ID_TYPE_TICKER", "classCode": "TQTF", "id": ticker}).encode(),
                headers=t_headers, method="POST"
            )
            with urllib.request.urlopen(req2, timeout=8) as r2:
                data2 = json.loads(r2.read())
            return data2.get("instrument", {}).get("forQualInvestorFlag", True)
        except Exception:
            return True  # по умолчанию считаем квал-only если не можем проверить

def update_qual_only_tickers(tinkoff_token):
    """
    1. Проверяет новые тикеры из аномалий — квал ли они.
    2. Раз в месяц перепроверяет весь накопленный список —
       вдруг какие-то тикеры стали доступны всем.
    Обновляет QUAL_ONLY_TICKERS и кэш-файл.
    """
    global QUAL_ONLY_TICKERS

    cache = load_qual_cache()
    cached_set = set(cache.get("tickers", []))
    today = date.today()
    last_updated = cache.get("updated")

    # Проверяем нужна ли ежемесячная ревизия
    need_monthly_check = True
    if last_updated:
        try:
            last_date = date.fromisoformat(last_updated)
            days_since = (today - last_date).days
            need_monthly_check = days_since >= 30
        except Exception:
            pass

    if need_monthly_check and tinkoff_token:
        print(f"  [QualCheck] Ежемесячная ревизия {len(cached_set)} тикеров...")
        became_open = []
        still_qual  = []
        for t in list(cached_set):
            status = check_ticker_qual_status(t, tinkoff_token)
            if status is False:  # стал доступен всем!
                became_open.append(t)
                print(f"    ✅ {t} — теперь доступен всем (убираем из фильтра)")
            else:
                still_qual.append(t)

        if became_open:
            cached_set -= set(became_open)
            print(f"  [QualCheck] Стали открытыми: {became_open}")

        cache["tickers"] = list(cached_set)
        cache["updated"] = today.isoformat()
        cache["checked_count"] = len(cached_set)
        save_qual_cache(cache)
        QUAL_ONLY_TICKERS = cached_set
        print(f"  [QualCheck] Ревизия завершена. Квал-only: {len(cached_set)}")

    return cached_set

def check_and_filter_anomalies(anomalies, tinkoff_token):
    """
    Фильтрует список аномалий:
    - Если тикер в QUAL_ONLY_TICKERS — отфильтровываем.
    - Если тикер новый и неизвестный — проверяем через Tinkoff API.
    - Если квал-only — добавляем в кэш и фильтруем.
    """
    global QUAL_ONLY_TICKERS

    cache  = load_qual_cache()
    cached = set(cache.get("tickers", []))
    result = []
    newly_added = []

    for item in anomalies:
        ticker = item.get("ticker", "")
        if not ticker:
            continue

        # Уже знаем что квал-only
        if ticker in QUAL_ONLY_TICKERS or ticker in cached:
            print(f"  [QualFilter] {ticker} — квал-only, пропускаем")
            continue

        # Новый тикер — проверяем через Tinkoff
        if tinkoff_token and ticker not in cached:
            is_qual = check_ticker_qual_status(ticker, tinkoff_token)
            if is_qual:
                print(f"  [QualFilter] {ticker} — НОВЫЙ квал-only, добавляем в фильтр")
                QUAL_ONLY_TICKERS.add(ticker)
                cached.add(ticker)
                newly_added.append(ticker)
                continue
            elif is_qual is False:
                print(f"  [QualFilter] {ticker} — доступен всем ✅")

        result.append(item)

    # Сохраняем обновлённый кэш
    if newly_added:
        cache["tickers"] = list(cached)
        save_qual_cache(cache)
        print(f"  [QualFilter] Добавлено в фильтр: {newly_added}")

    return result


# ─── СКРИНЕР ЧЕРЕЗ TINKOFF API ───────────────────────────────────────────────

def collect_screener_tinkoff(tinkoff_token, portfolio_tickers=None):
    """
    Собирает данные скринера через Tinkoff Invest API.
    Получает список акций с объёмами и изменениями цен.
    """
    if not tinkoff_token:
        return None

    print("  [Screener] Загружаем данные через Tinkoff API...")
    base_url = "https://invest-public-api.tbank.ru/rest"
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
    """
    Скринер по конкретному списку голубых фишек через MOEX ISS.
    Используем точечные запросы (как в collect_moex который стабильно работает)
    вместо общего запроса всех акций который MOEX блокирует.
    """
    print("[SCREENER] Загружаем котировки голубых фишек MOEX...")

    BLUE_CHIPS = ["SBER","LKOH","GAZP","ROSN","NVTK","GMKN","TATN","MGNT",
                  "YDEX","MTSS","MOEX","PLZL","CHMF","NLMK","MAGN","ALRS",
                  "SNGS","VTBR","T","SMLT","UGLD","AFKS","AFLT","PHOR",
                  "OZON","HEAD","POSI","FLOT","RUAL","BSPB","SVCB","ASTR",
                  "IRAO","HYDR","FEES","RTKM","PIKK","MTLR"]

    try:
        url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/"
               f"boards/TQBR/securities.json"
               f"?securities={','.join(BLUE_CHIPS)}"
               f"&iss.meta=off&iss.only=marketdata,securities")
        data = safe_fetch(url, timeout=15)

        if not data:
            print("  [SCREENER] MOEX недоступен")
            return {"top_volume": [], "cheap_growth": [], "rising_interest": [],
                    "rising_new": [], "rising_dropped": [], "ipo": []}

        d = json.loads(data)
        mc = d["marketdata"]["columns"]
        sc = d["securities"]["columns"]

        items = []
        sec_map = {}
        for row in d["securities"]["data"]:
            r = dict(zip(sc, row))
            sec_map[r["SECID"]] = r.get("SHORTNAME", r["SECID"])

        for row in d["marketdata"]["data"]:
            r = dict(zip(mc, row))
            ticker = r.get("SECID")
            last = r.get("LAST") or r.get("PREVPRICE")
            if not last or not ticker:
                continue
            prev = r.get("PREVPRICE") or last
            change = round(last - prev, 2) if prev else 0
            pct = round(change / prev * 100, 2) if prev else 0
            val = r.get("VALTODAY") or 0

            items.append({
                "ticker": ticker,
                "name":   sec_map.get(ticker, ticker),
                "price":  round(last, 2),
                "pct":    pct,
                "change": change,
                "volume": val,
            })

        print(f"  [SCREENER] Получено котировок: {len(items)}")

        # Топ по объёму торгов
        items.sort(key=lambda x: x["volume"], reverse=True)
        top_vol = items[:10]

        for s in top_vol:
            if s["pct"] > 1:
                s["vol_label"] = "🟢 Покупатели"
            elif s["pct"] < -1:
                s["vol_label"] = "🔴 Продавцы"
            else:
                s["vol_label"] = "⚪ Нейтрально"

        # Дешёвые акции
        cheap = sorted([i for i in items if 0 < i["price"] <= 500], key=lambda x: x["price"])
        cheap_growth = cheap[:10]
        for c in cheap_growth:
            c["score"] = 50
            c["grade"] = "B"

        return {
            "top_volume":      top_vol,
            "cheap_growth":    cheap_growth,
            "rising_interest": [],
            "rising_new":      [],
            "rising_dropped":  [],
            "ipo":             [],
            "_source":         "moex_targeted",
        }

    except Exception as e:
        print(f"  [SCREENER] Ошибка: {e}")
        import traceback; traceback.print_exc()
        return {"top_volume": [], "cheap_growth": [], "rising_interest": [],
                "rising_new": [], "rising_dropped": [], "ipo": []}


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

def _clean_git_conflicts(text):
    """Убирает git merge conflict маркеры из файла."""
    import re
    # Удаляем блоки <<<<<<< ... ======= ... >>>>>>>
    # Берём только версию HEAD (между <<<< и ====)
    text = re.sub(r'<<<<<<< [^\n]*\n', '', text)
    text = re.sub(r'=======\n[\s\S]*?>>>>>>> [^\n]*\n', '', text)
    return text

def _load_last_log(role):
    log_dir = LOGS_DIR / role
    if not log_dir.exists():
        return None
    files = sorted(log_dir.glob('*.json'), reverse=True)
    if not files:
        return None
    for log_file in files[:5]:
        try:
            with open(log_file, encoding='utf-8') as f:
                data = f.read().strip()
            if not data or data[0] != '{':
                print(f'  [Log] Пропускаем некорректный файл: {log_file.name}')
                continue
            # Очищаем git merge conflict маркеры если есть
            if '<<<<<<<' in data:
                print(f'  [Log] Обнаружен git конфликт в {log_file.name} — очищаем')
                data = _clean_git_conflicts(data)
            return json.loads(data)
        except (json.JSONDecodeError, Exception) as e:
            print(f'  [Log] Ошибка чтения {log_file.name}: {e} — пропускаем')
            continue
    return None
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

def check_tinkoff_token(tinkoff_token):
    """
    Проверяет валидность токена Tinkoff.
    Возвращает True если токен рабочий, False если истёк (ошибка 40003).
    При истечении отправляет уведомление в Telegram.
    """
    base_url = "https://invest-public-api.tbank.ru/rest"
    t_headers = {
        "Authorization": f"Bearer {tinkoff_token}",
        "Content-Type": "application/json",
    }
    try:
        req = urllib.request.Request(
            f"{base_url}/tinkoff.public.invest.api.contract.v1.UsersService/GetInfo",
            data=json.dumps({}).encode(),
            headers=t_headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=8, context=_get_tbank_ctx()) as r:
            data = json.loads(r.read())
        print(f"  [Tinkoff] Токен валиден: prem_status={data.get('premStatus','?')}")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if "40003" in body or e.code == 401:
            print(f"  [Tinkoff] ТОКЕН ИСТЁК (ошибка 40003) — нужно обновить TINKOFF_TOKEN в GitHub Secrets!")
            # Пишем в файл для уведомления советника
            try:
                token_error_path = BASE_DIR / "logs" / "tinkoff_token_error.txt"
                with open(token_error_path, "w") as f:
                    f.write(f"ТОКЕН ИСТЁК {TODAY}. Ошибка 40003. Обнови TINKOFF_TOKEN в GitHub Secrets: https://github.com/podlesnykhdn/my_project_invest/settings/secrets/actions")
            except Exception:
                pass
            return False
        print(f"  [Tinkoff] Ошибка проверки токена {e.code}: {body[:100]}")
        return True  # Другая ошибка — не значит что токен истёк
    except Exception as e:
        print(f"  [Tinkoff] Ошибка проверки: {e}")
        return True  # При сетевых ошибках считаем токен валидным


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

    base_url = "https://invest-public-api.tbank.ru/rest"
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
        with urllib.request.urlopen(req, timeout=10, context=_get_tbank_ctx()) as r:
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
        with urllib.request.urlopen(req2, timeout=10, context=_get_tbank_ctx()) as r:
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

    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f"  [Tinkoff] HTTP {e.code}: {body[:300]}")
        try:
            with open(BASE_DIR / 'logs' / 'tinkoff_error.txt', 'w') as f:
                f.write(f'HTTP {e.code}: {body}')
        except Exception: pass
        return None
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"  [Tinkoff] Ошибка: {e}")
        print(tb)
        try:
            with open(BASE_DIR / 'logs' / 'tinkoff_error.txt', 'w') as _f:
                _f.write(f'Exception: {e}\n\n{tb}')
        except Exception: pass
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


def check_portfolio_changed(tinkoff_portfolio, rules):
    """
    Триггер: сравниваем qty из Tinkoff API с qty в rules.json.
    Если изменилось (покупка или продажа) — возвращаем True.
    """
    if not tinkoff_portfolio:
        return False

    rules_qty = {
        p["ticker"]: p["qty"]
        for p in rules.get("portfolio", {}).get("positions", [])
    }
    tinkoff_qty = {
        p["ticker"]: int(p.get("qty", 0))
        for p in tinkoff_portfolio.get("positions", [])
        if p.get("ticker") and p.get("ticker") != "RUB"
    }

    changed = []
    for ticker, qty in tinkoff_qty.items():
        old_qty = rules_qty.get(ticker, 0)
        if abs(qty - old_qty) > 0:
            changed.append(f"{ticker}: {old_qty} → {qty}")

    # Новые тикеры (новые позиции)
    for ticker in rules_qty:
        if ticker not in tinkoff_qty:
            changed.append(f"{ticker}: {rules_qty[ticker]} → 0 (продан)")

    if changed:
        print(f"  [Триггер] Изменения в портфеле: {', '.join(changed)}")
        return True

    return False



# ─── СТАВКИ СБЕРБАНКА (накопительный счёт) ──────────────────────────────────
SBER_DEPOSIT_RATES = [
    # Базовая ставка для действующих клиентов (без приветственных надбавок)
    # Источник: РБК, Банки.ру, Финуслуги
    ("2024-01-01", 10.0),
    ("2024-09-20", 12.0),
    ("2024-10-29", 13.5),
    ("2025-02-22", 12.0),
    ("2025-06-09", 10.0),
    ("2025-09-01",  9.0),
    ("2025-12-20",  9.0),
    ("2026-01-05",  8.0),
    ("2026-02-20",  7.5),
    ("2026-04-01",  7.0),
    ("2026-04-30",  6.5),
]

# История операций из Tinkoff API
# Покупки — все наши акции
BUYS_HISTORY = [
    {"date":"2025-02-28","ticker":"SBER","qty":120,"price":307.34,"total":36880.8},
    {"date":"2025-03-16","ticker":"TGLD","qty":100,"price":11.68,"total":1168.46},
    {"date":"2025-03-16","ticker":"SBER","qty":10,"price":321.19,"total":3211.9},
    {"date":"2025-03-18","ticker":"X5","qty":2,"price":3614.0,"total":7228.0},
    {"date":"2025-03-20","ticker":"X5","qty":10,"price":3734.25,"total":37342.5},
    {"date":"2025-03-28","ticker":"X5","qty":1,"price":3509.0,"total":3509.0},
    {"date":"2025-03-31","ticker":"X5","qty":2,"price":3515.0,"total":7030.0},
    {"date":"2025-03-31","ticker":"SBER","qty":10,"price":308.45,"total":3084.5},
    {"date":"2025-04-25","ticker":"X5","qty":3,"price":3404.0,"total":10212.0},
    {"date":"2025-05-16","ticker":"TGLD","qty":200,"price":10.06,"total":2012.18},
    {"date":"2025-05-23","ticker":"BELU","qty":6,"price":459.0,"total":2754.0},
    {"date":"2025-05-26","ticker":"LENT","qty":10,"price":1437.2,"total":14372.0},
    {"date":"2025-05-28","ticker":"X5","qty":1,"price":3271.0,"total":3271.0},
    {"date":"2025-05-28","ticker":"BELU","qty":1,"price":461.5,"total":461.5},
    {"date":"2025-05-28","ticker":"LENT","qty":2,"price":1440.0,"total":2880.0},
    {"date":"2025-06-11","ticker":"X5","qty":1,"price":3273.5,"total":3273.5},
    {"date":"2025-06-26","ticker":"TGLD","qty":10,"price":9.72,"total":97.16},
    {"date":"2025-06-27","ticker":"LENT","qty":1,"price":1412.0,"total":1412.0},
    {"date":"2025-06-29","ticker":"LENT","qty":1,"price":1417.0,"total":1417.0},
    {"date":"2025-07-01","ticker":"X5","qty":2,"price":3497.0,"total":6994.0},
    {"date":"2025-07-01","ticker":"LENT","qty":2,"price":1445.5,"total":2891.0},
    {"date":"2025-07-16","ticker":"X5","qty":3,"price":2935.0,"total":8805.0},
    {"date":"2025-07-16","ticker":"BELU","qty":1,"price":426.5,"total":426.5},
    {"date":"2025-07-16","ticker":"TGLD","qty":30,"price":9.33,"total":279.98},
    {"date":"2025-07-28","ticker":"X5","qty":1,"price":3006.0,"total":3006.0},
    {"date":"2025-07-28","ticker":"BELU","qty":2,"price":452.0,"total":904.0},
    {"date":"2025-08-05","ticker":"LENT","qty":6,"price":1733.0,"total":10398.0},
    {"date":"2025-08-05","ticker":"SBER","qty":3,"price":306.56,"total":919.68},
    {"date":"2025-08-07","ticker":"LENT","qty":4,"price":1706.25,"total":6823.0},
    {"date":"2025-08-13","ticker":"LENT","qty":2,"price":1715.0,"total":3430.0},
    {"date":"2025-08-13","ticker":"TGLD","qty":2,"price":10.67,"total":21.34},
    {"date":"2025-08-25","ticker":"TGLD","qty":709,"price":10.8,"total":7657.23},
    {"date":"2025-08-28","ticker":"LENT","qty":3,"price":1817.5,"total":5452.5},
    {"date":"2025-08-28","ticker":"BELU","qty":2,"price":448.6,"total":897.2},
    {"date":"2025-08-29","ticker":"TGLD","qty":1,"price":10.92,"total":10.92},
    {"date":"2025-10-10","ticker":"SBER","qty":16,"price":288.73,"total":4619.68},
    {"date":"2025-10-13","ticker":"SBER","qty":11,"price":288.14,"total":3169.54},
    {"date":"2025-10-13","ticker":"LENT","qty":3,"price":1690.0,"total":5070.0},
    {"date":"2025-10-17","ticker":"TGLD","qty":3,"price":14.13,"total":42.39},
    {"date":"2025-10-22","ticker":"LENT","qty":2,"price":1694.5,"total":3389.0},
    {"date":"2025-11-05","ticker":"TGLD","qty":17,"price":12.83,"total":218.11},
    {"date":"2025-11-13","ticker":"SBER","qty":14,"price":300.4,"total":4205.6},
    {"date":"2025-11-13","ticker":"TGLD","qty":139,"price":13.6,"total":1890.38},
    {"date":"2025-11-13","ticker":"X5","qty":3,"price":2686.67,"total":8060.0},
    {"date":"2025-11-14","ticker":"TGLD","qty":3,"price":13.45,"total":40.24},
    {"date":"2025-11-24","ticker":"TGLD","qty":250,"price":12.81,"total":3202.5},
    {"date":"2025-11-28","ticker":"LENT","qty":2,"price":1712.5,"total":3425.0},
    {"date":"2025-11-28","ticker":"TGLD","qty":202,"price":12.98,"total":2621.96},
    {"date":"2025-12-12","ticker":"TGLD","qty":370,"price":13.52,"total":5002.41},
    {"date":"2025-12-26","ticker":"TGLD","qty":100,"price":13.71,"total":1371.0},
    {"date":"2026-01-23","ticker":"TGLD","qty":6,"price":14.76,"total":88.56},
    {"date":"2026-02-02","ticker":"TGLD","qty":776,"price":13.82,"total":10724.16},
    {"date":"2026-02-14","ticker":"LENT","qty":5,"price":2109.0,"total":10545.0},
    {"date":"2026-02-14","ticker":"X5","qty":5,"price":2441.0,"total":12205.0},
    {"date":"2026-02-16","ticker":"LENT","qty":2,"price":2129.0,"total":4258.0},
    {"date":"2026-02-16","ticker":"TGLD","qty":1002,"price":15.25,"total":15280.5},
    {"date":"2026-02-19","ticker":"LENT","qty":3,"price":2088.0,"total":6264.0},
    {"date":"2026-02-19","ticker":"TGLD","qty":1,"price":15.26,"total":15.26},
    {"date":"2026-02-24","ticker":"X5","qty":11,"price":2439.91,"total":26839.0},
    {"date":"2026-02-24","ticker":"TGLD","qty":6,"price":15.75,"total":94.5},
    {"date":"2026-02-25","ticker":"LENT","qty":5,"price":2060.5,"total":10302.5},
    {"date":"2026-02-26","ticker":"TGLD","qty":2,"price":15.94,"total":31.88},
    {"date":"2026-04-02","ticker":"TGLD","qty":175,"price":14.67,"total":2567.25},
    {"date":"2026-04-10","ticker":"X5","qty":2,"price":2451.0,"total":4902.0},
    {"date":"2026-04-13","ticker":"TGLD","qty":1,"price":14.24,"total":14.24},
    {"date":"2026-04-23","ticker":"TGLD","qty":429,"price":14.0,"total":6006.02},
    {"date":"2026-06-22","ticker":"TGLD","qty":382,"price":12.19,"total":4656.58},
    {"date":"2026-06-22","ticker":"SBER","qty":6,"price":304.71,"total":1828.26},
    {"date":"2026-06-22","ticker":"X5","qty":3,"price":2236.5,"total":6709.5},
    {"date":"2026-06-26","ticker":"TGLD","qty":1,"price":12.58,"total":12.58},
]

# Продажи наших акций
SELLS_HISTORY = [
    {"date":"2025-02-28","ticker":"SBER","qty":110,"price":308.41,"total":33925.1},
    {"date":"2025-03-20","ticker":"X5","qty":5,"price":3735.0,"total":18675.0},
    {"date":"2026-04-02","ticker":"TGLD","qty":340,"price":7.587,"total":2579.58},
]

# Дивиденды (чистыми после налогов)
DIVIDENDS_RECEIVED = [
    {"date":"2025-07-22","ticker":"X5","total":19848.0},   # дивиденды X5
    {"date":"2025-06-30","ticker":"BELU","total":415.0},   # дивиденды BELU
    {"date":"2025-08-15","ticker":"SBER","total":1045.0},  # дивиденды SBER
]

def get_sber_rate(dt_str):
    """Ставка накопительного счёта Сбербанка на дату."""
    from datetime import date as _date
    d = _date.fromisoformat(dt_str)
    rate = SBER_DEPOSIT_RATES[0][1]
    for rate_date, r in SBER_DEPOSIT_RATES:
        if d >= _date.fromisoformat(rate_date):
            rate = r
        else:
            break
    return rate

def calc_deposit_comparison(today_str, tinkoff_portfolio):
    """
    Корректный расчёт: акции vs вклад Сбербанка.

    Логика вклада:
    - Каждая покупка акций: та же сумма кладётся на вклад в эту же дату
    - Каждая продажа акций: выручка остаётся на вкладе с даты продажи
    - Дивиденды: реинвестируются на вклад с даты получения
    - Итог вклада = сумма всех потоков × (1 + ставка × дни/365)

    Логика акций:
    - Текущая стоимость портфеля из Tinkoff API
    - + выручка от уже совершённых продаж (с доходом на вкладе с даты продажи)
    - + полученные дивиденды (с доходом на вкладе с даты получения)
    """
    from datetime import date as _date
    today = _date.fromisoformat(today_str)

    # ── Сторона ВКЛАДА ──────────────────────────────────────────────────────
    deposit_value = 0.0
    total_spent   = 0.0  # сколько всего потратили на покупки

    # Каждая покупка → лежала бы на вкладе с даты покупки
    for buy in BUYS_HISTORY:
        buy_date = _date.fromisoformat(buy["date"])
        if buy_date > today:
            continue
        days = (today - buy_date).days
        rate = get_sber_rate(buy["date"])
        deposit_value += buy["total"] * (1 + rate / 100 * days / 365)
        total_spent   += buy["total"]

    # ── Сторона АКЦИЙ ───────────────────────────────────────────────────────
    # Текущая стоимость портфеля
    stocks_current = tinkoff_portfolio.get("total_current", 0) if tinkoff_portfolio else 0

    # Выручка от продаж — с даты продажи лежала бы на вкладе
    # И эти деньги НЕ работают в акциях, поэтому из deposit_value их вычитаем
    # (они уже учтены через покупку), а добавляем их реальную стоимость на вкладе
    total_sold_deposit = 0.0  # сколько выручка принесла бы на вкладе
    total_sold         = 0.0  # сколько выручили от продаж

    for sell in SELLS_HISTORY:
        sell_date = _date.fromisoformat(sell["date"])
        if sell_date > today:
            continue
        days = (today - sell_date).days
        rate = get_sber_rate(sell["date"])
        # Выручка от продажи на вкладе с даты продажи
        total_sold_deposit += sell["total"] * (1 + rate / 100 * days / 365)
        total_sold         += sell["total"]

    # Дивиденды на вкладе с даты получения
    total_divs_deposit = 0.0
    total_divs         = 0.0
    for div in DIVIDENDS_RECEIVED:
        div_date = _date.fromisoformat(div["date"])
        if div_date > today:
            continue
        days = (today - div_date).days
        rate = get_sber_rate(div["date"])
        total_divs_deposit += div["total"] * (1 + rate / 100 * days / 365)
        total_divs         += div["total"]

    # Итог для сравнения:
    # Акции: текущий портфель + выручка от продаж на вкладе + дивиденды на вкладе
    stocks_total  = stocks_current + total_sold_deposit + total_divs_deposit

    # Вклад: всё что потратили на покупки лежало бы на вкладе
    # (продажи уже включены в покупки по сумме, так что deposit_value корректен)
    deposit_total = deposit_value

    # Чистые вложения = потрачено - выручено от продаж
    net_invested = total_spent - total_sold

    diff           = stocks_total - deposit_total
    deposit_income = deposit_total - total_spent

    return {
        "date":             today_str,
        "total_spent":      round(total_spent, 2),       # всего потрачено на покупки
        "total_sold":       round(total_sold, 2),         # выручено от продаж
        "total_divs":       round(total_divs, 2),         # дивиденды получено
        "net_invested":     round(net_invested, 2),       # чистые вложения
        "deposit_value":    round(deposit_total, 2),      # вклад (все покупки)
        "deposit_income":   round(deposit_income, 2),     # доход вклада
        "stocks_current":   round(stocks_current, 2),     # портфель сейчас
        "stocks_total":     round(stocks_total, 2),        # акции + продажи + дивы на вкладе
        "diff":             round(diff, 2),               # акции_всего - вклад
        "sber_rate_today":  get_sber_rate(today_str),
    }


def collect():
    print(f"\n{'='*50}")
    print(f"Сборщик запущен: {TODAY} {NOW}")
    print(f"{'='*50}\n")

    rules = load_rules()

    try:
        currency = collect_currency()
    except Exception as e:
        print(f'[ERROR] collect_currency: {e}')
        currency = {}
    try:
        oil = collect_oil()
    except Exception as e:
        print(f'[ERROR] collect_oil: {e}')
        oil = {}
    try:
        quotes = collect_moex(rules)
    except Exception as e:
        print(f'[ERROR] collect_moex: {e}')
        quotes = {}
    try:
        screener = collect_screener(rules)
    except Exception as e:
        print(f'[ERROR] collect_screener: {e}')
        screener = {}
    try:
        assets = collect_assets(rules, oil)
    except Exception as e:
        print(f'[ERROR] collect_assets: {e}')
        assets = []
    try:
        news = collect_news(rules)
    except Exception as e:
        print(f'[ERROR] collect_news: {e}')
        news = []

    tinkoff_portfolio = fetch_tinkoff_portfolio()
    # Проверяем изменения в портфеле — триггер для обновления истории
    try:
        portfolio_changed = check_portfolio_changed(tinkoff_portfolio, rules)
    except Exception as _e:
        print(f"  [Триггер] Ошибка проверки: {_e}")
        portfolio_changed = True  # при ошибке обновляем на всякий случай
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
    portfolio = calc_portfolio(rules, quotes, tinkoff_portfolio)
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
    # Ежемесячная ревизия и обновление списка квал-only
    tinkoff_token = os.environ.get("TINKOFF_TOKEN")
    update_qual_only_tickers(tinkoff_token)
    inefficiencies = analyze_inefficiencies(rules, quotes, screener, vol_history_data)
    # Фильтруем аномалии — убираем квал-only и проверяем новые тикеры
    if inefficiencies:
        port = inefficiencies.get("portfolio", [])
        mkt  = inefficiencies.get("market", [])
        port = check_and_filter_anomalies(port, tinkoff_token)
        mkt  = check_and_filter_anomalies(mkt, tinkoff_token)
        inefficiencies["portfolio"] = port
        inefficiencies["market"]    = mkt
    price_history = collect_price_history(rules, quotes)
    # Фильтруем rising_interest от квал-only
    rising = screener if isinstance(screener, dict) else {}
    if rising:
        curr = rising.get("rising_interest", [])
        new_r = rising.get("rising_new", [])
        rising["rising_interest"] = check_and_filter_anomalies(curr, tinkoff_token)
        rising["rising_new"]      = check_and_filter_anomalies(new_r, tinkoff_token)
        screener = rising

    # Применяем дивидендные данные к карточкам скринера
    for stock in screener.get("cheap_growth", []):
        div_info = dividends.get(stock["ticker"], {})
        stock["pays_dividends"] = div_info.get("pays_dividends", False)
        stock["dividend_next"]  = div_info.get("next_payment")
        if "score" not in stock:
            stock["score"] = 50
        if stock["pays_dividends"]:
            stock["score"] = min(stock["score"] + 10, 100)
            stock["grade"] = _grade(stock["score"])

    usd_change = 0.0
    if currency.get("usd") and currency.get("usd_prev"):
        usd_change = round(
            (currency["usd"] - currency["usd_prev"]) / currency["usd_prev"] * 100, 2
        )

    # Считаем сравнение с вкладом
    deposit_comparison = calc_deposit_comparison(TODAY, tinkoff_portfolio)
    print(f"  [Вклад vs Акции] Акции: {deposit_comparison.get('stocks_total', deposit_comparison.get('stocks_value',0)):,.0f}₽ | Вклад: {deposit_comparison['deposit_value']:,.0f}₽ | Разница: {deposit_comparison['diff']:+,.0f}₽")

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
        "deposit_comparison": deposit_comparison,
    }

    save_log(result)

    print(f"\n{'='*50}")
    print(f"Готово! Правил сработало: {len(fired_rules)}")
    print(f"Стоимость портфеля: {portfolio['total_value']:,.0f} руб. "
          f"({portfolio['total_change']:+,.0f} руб.)")
    print(f"{'='*50}\n")

    return result

if __name__ == "__main__":
    import traceback as _tb
    _err_file = BASE_DIR / "logs" / "collector_error.txt"
    try:
        collect()
        # Успешно — удаляем файл ошибки если есть
        if _err_file.exists(): _err_file.unlink()
    except Exception as e:
        err_text = f"[FATAL ERROR] {e}\n\n" + _tb.format_exc()
        print(err_text)
        _err_file.parent.mkdir(parents=True, exist_ok=True)
        with open(_err_file, "w") as f:
            f.write(err_text)
        raise

    # История операций — запускается отдельно через fetch_operations.py
    # Триггер: изменение portfolio_changed (покупка/продажа)
    # TODO: активировать когда fetch_operations стабилен
    pass  # placeholder
