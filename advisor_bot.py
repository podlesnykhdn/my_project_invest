"""
advisor_bot.py — Telegram бот Советника
Команды: /start, /advice, /portfolio, /screener, /top
Утренняя сводка + мгновенные алерты.
"""

import os
import json
import urllib.request
import re
from datetime import date, datetime
from pathlib import Path

TOKEN    = os.environ["ADVISOR_BOT_TOKEN"]
CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs" / "advisor"
TODAY    = date.today().isoformat()
WEEKDAY  = datetime.now().weekday()
MODE     = os.environ.get("BOT_MODE", "morning")

WEEKDAYS_RU = ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]

# ─── TELEGRAM API ─────────────────────────────────────────────────────────────

def tg(method, data):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def send(text, parse_mode="HTML"):
    # Telegram ограничение 4096 символов
    if len(text) > 4000:
        text = text[:3990] + "\n\n<i>...сообщение обрезано</i>"
    return tg("sendMessage", {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })

# ─── ЛОГИ ─────────────────────────────────────────────────────────────────────

def load_log():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{TODAY}.json"
    if log_file.exists():
        with open(log_file, encoding="utf-8") as f:
            return json.load(f)
    return {"alerts_sent": 0, "morning_sent": False, "alerts": []}

def save_log(data):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOGS_DIR / f"{TODAY}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def load_collector_data():
    """Загружаем последние данные от сборщика."""
    clog_dir = BASE_DIR / "logs" / "collector"
    if not clog_dir.exists():
        return None
    files = sorted(clog_dir.glob("*.json"))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        data = json.load(f)
    # Проверяем свежесть данных
    data_date = data.get("meta", {}).get("date", "")
    data["_is_fresh"] = (data_date == TODAY)
    data["_data_date"] = data_date
    return data

# ─── ФОРМАТИРОВАНИЕ ───────────────────────────────────────────────────────────

SIGNAL_EMOJI = {
    "positive": "🟢", "negative": "🔴", "neutral": "⚪",
    "warning": "⚠️", "danger": "🚨", "info": "ℹ️",
    "mixed": "🟡", "ok": "✅", "high_risk": "🔴", "low_risk": "🟢",
}

def fmt(n):
    if n is None: return "—"
    return f"{n:,.0f}".replace(",", " ")

def fmt_rub(n):
    if n is None: return "—"
    return f"{n:+,.0f} ₽".replace(",", " ")

def fmt_price(n):
    if not n: return "—"
    return f"{n:,.2f} ₽".replace(",", " ")

def fmt_vol(v):
    if not v: return "—"
    if v >= 1e9: return f"{v/1e9:.1f}B ₽"
    if v >= 1e6: return f"{v/1e6:.0f}M ₽"
    if v >= 1e3: return f"{v/1e3:.0f}K ₽"
    return f"{v:.0f} ₽"


def build_conclusion(data, fired_rules, portfolio_signals):
    """Формирует чёткий итоговый вывод для инвестора."""
    if not data:
        return "\n─ ─ ─\n❓ <b>Итог:</b> Нет данных для анализа."

    # Считаем сигналы по портфелю
    danger_count  = 0
    positive_count = 0
    risk_tickers  = []
    opportunity_tickers = []

    for ticker, signals in portfolio_signals.items():
        for s in signals:
            sig = s.get("signal", "")
            if sig in ("negative", "high_risk", "danger"):
                danger_count += 1
                risk_tickers.append(ticker)
            elif sig in ("positive",):
                positive_count += 1
                opportunity_tickers.append(ticker)

    # Проверяем скринер на акции с оценкой A
    screener = data.get("screener", {})
    cheap = screener.get("cheap_growth", [])
    grade_a = [s for s in cheap if s.get("score", 0) >= 75]
    grade_b = [s for s in cheap if 50 <= s.get("score", 0) < 75]

    # Проверяем критические правила
    critical_rules = [r for r in fired_rules if r.get("signal") in ("danger",)]
    warning_rules  = [r for r in fired_rules if r.get("signal") in ("warning",)]

    # Определяем итоговый вывод
    lines = ["\n─ ─ ─\n"]

    # СЦЕНАРИЙ 1: Есть критические риски по портфелю
    if critical_rules or danger_count >= 2:
        lines.append("⚠️ <b>ИТОГ: Есть риск — требует внимания</b>")
        lines.append("")
        if critical_rules:
            lines.append(f"Сработало критических правил: {len(critical_rules)}")
        if risk_tickers:
            unique_risk = list(dict.fromkeys(risk_tickers))
            lines.append(f"Под давлением: {', '.join(unique_risk)}")
        lines.append("")
        lines.append("<b>Что делать:</b> Не паниковать. Долгосрочная стратегия выдерживает краткосрочные потрясения. Наблюдай за развитием событий.")

    # СЦЕНАРИЙ 2: Есть акция с оценкой A в скринере
    elif grade_a:
        s = grade_a[0]
        lines.append("💡 <b>ИТОГ: Есть возможность — рассмотри докупку</b>")
        lines.append("")
        lines.append(f"Акция с высоким потенциалом: <b>{s['ticker']}</b> ({s.get('name','')[:20]})")
        lines.append(f"Цена: {fmt_price(s['price'])}  Score: {s['score']}/100")
        lines.append(f"Объём: {fmt_vol(s['volume'])}")
        lines.append("")
        lines.append("<b>Что делать:</b> Изучи компанию подробнее. Если бизнес понятен и новостей негативных нет — можно рассмотреть небольшую позицию.")

    # СЦЕНАРИЙ 3: Есть предупреждения или акции с оценкой B
    elif warning_rules or danger_count == 1 or grade_b:
        lines.append("👀 <b>ИТОГ: Есть сигнал — наблюдай</b>")
        lines.append("")
        if warning_rules:
            lines.append(f"Предупреждений: {len(warning_rules)}")
        if danger_count == 1 and risk_tickers:
            lines.append(f"Под лёгким давлением: {risk_tickers[0]}")
        if grade_b:
            s = grade_b[0]
            lines.append(f"Интересная акция на радаре: <b>{s['ticker']}</b> — {fmt_price(s['price'])}  Score: {s['score']}/100")
        lines.append("")
        lines.append("<b>Что делать:</b> Портфель в порядке, но следи за развитием. Пока ничего менять не нужно.")

    # СЦЕНАРИЙ 4: Всё спокойно
    else:
        lines.append("🟢 <b>ИТОГ: Всё спокойно — держи портфель</b>")
        lines.append("")

        # Дивидендный сезон
        import datetime
        month = datetime.datetime.now().month
        if month in [5, 6, 7]:
            lines.append("📅 Дивидендный сезон: жди выплат от X5, Сбера и Novabev.")
            lines.append("")

        lines.append("<b>Что делать:</b> Стратегия работает. Продавать ничего не нужно. Если есть свободные деньги — можно докупить дивидендные позиции на просадках.")

    return "\n".join(lines)


def build_morning_report(data):
    if not data:
        return (
            "📊 <b>Советник — утренняя сводка</b>\n"
            "{'─' * 28}\n"
            "⚠️ Данные сборщика недоступны.\n"
            "Запусти сборщик вручную через GitHub Actions."
        )

    today_str = datetime.now().strftime("%d.%m.%Y")
    wd = WEEKDAYS_RU[WEEKDAY]
    lines = [f"📊 <b>Советник — {today_str}, {wd}</b>"]

    # Предупреждение если данные из кэша
    if not data.get("_is_fresh"):
        lines.append(f"\n⚠️ <b>ВНИМАНИЕ: данные от {data.get('_data_date', '?')} (кэш)</b>")

    lines.append("─" * 28)

    # 1. ПОРТФЕЛЬ
    portfolio = data.get("portfolio", {})
    if portfolio:
        tv = portfolio.get("total_value", 0)
        tc = portfolio.get("total_change", 0)
        tp = portfolio.get("total_pct", 0)
        sign = "📈" if tc >= 0 else "📉"
        lines.append(f"\n💼 <b>Портфель</b>")
        lines.append(f"{sign} Стоимость: <b>{tv:,.0f} ₽</b>  ({fmt_rub(tc)} / {tp:+.1f}%)")
        lines.append("")
        for pos in portfolio.get("positions", []):
            sig = "📈" if pos["change"] >= 0 else "📉"
            lines.append(
                f"  {sig} <b>{pos['ticker']}</b>: {fmt_price(pos['price'])}  "
                f"({fmt_rub(pos['day_rub'])} / {pos['pct']:+.1f}%)"
            )

    # 2. МАКРО
    lines.append(f"\n─ ─ ─\n📡 <b>Макро</b>")
    curr = data.get("currency", {})
    oil  = data.get("oil", {})

    usd = curr.get("usd")
    usd_ch = curr.get("usd_change", 0)
    curr_src = curr.get("source", "ЦБ РФ")
    usd_flag = "⚠️ кэш" if curr.get("is_cached") else ""
    if usd:
        lines.append(f"  💵 USD: <b>{usd} ₽</b>  ({usd_ch:+.1f}%)  {usd_flag}")

    cny = curr.get("cny")
    if cny:
        lines.append(f"  🇨🇳 CNY: <b>{cny} ₽</b>")

    oil_price = oil.get("price")
    oil_src   = oil.get("source", "")
    oil_flag  = "⚠️ кэш" if oil.get("is_cached") else ""
    if oil_price:
        lines.append(f"  🛢 Нефть Brent: <b>${oil_price}</b>  {oil_flag}")
        if oil_flag:
            lines.append(f"  <i>Источник: {oil_src}</i>")

    # 3. СРАБОТАВШИЕ ПРАВИЛА
    fired = data.get("rules_fired", [])
    if fired:
        lines.append(f"\n─ ─ ─\n⚡ <b>Сработало правил: {len(fired)}</b>")
        for rule in fired[:5]:
            emoji = SIGNAL_EMOJI.get(rule.get("signal", ""), "•")
            lines.append(f"  {emoji} {rule.get('message', '')[:120]}")
    else:
        lines.append(f"\n─ ─ ─\n✅ <b>Значимых событий не выявлено</b>")

    # 4. СОВЕТЫ ПО ПОРТФЕЛЮ
    p_signals = data.get("portfolio_signals", {})
    if p_signals:
        lines.append(f"\n─ ─ ─\n🎯 <b>По позициям</b>")
        ticker_names = {
            "X5":   "ИКС 5 (Пятёрочка/Перекрёсток)",
            "LENT": "Лента",
            "SBER": "Сбербанк",
            "BELU": "НоваБев Групп",
            "TGLD": "ТБанк Золото (БПИФ)"
        }
        for ticker, signals in p_signals.items():
            if not signals:
                continue
            top = signals[0]
            emoji = SIGNAL_EMOJI.get(top.get("signal", "neutral"), "⚪")
            reason = top.get("reason", "")[:80]
            name = ticker_names.get(ticker, ticker)
            lines.append(f"  {emoji} <b>{name}</b>: {reason}")

    # 4.5. ДИВИДЕНДНЫЙ КАЛЕНДАРЬ
    dividends = data.get("dividends", {})
    upcoming = []
    for ticker, info in dividends.items():
        src = info.get("next_payment") or info.get("announced")
        if not src:
            continue
        d = src.get("days_to_record")
        if d is not None and 0 <= d <= 45:
            upcoming.append((d, ticker, src, info.get("next_payment") is not None))
    upcoming.sort(key=lambda x: x[0])

    if upcoming:
        lines.append(f"\n─ ─ ─\n📅 <b>Дивидендный календарь</b>")
        for days, ticker, info, is_confirmed in upcoming[:3]:
            amt = info.get("amount_per_share")
            shares = info.get("your_shares", 0)
            rec_date = info.get("record_date")
            pay_date = info.get("payment_date")
            label = "" if is_confirmed else " (анонс)"

            lines.append(f"  💰 <b>{ticker}</b>{label}")
            if rec_date and days is not None and days >= 0:
                lines.append(f"     📌 Отсечка: через {days} дн. ({rec_date})")
            if pay_date:
                lines.append(f"     💵 Выплата: ориентировочно {pay_date}")
            elif rec_date:
                lines.append(f"     💵 Выплата: ~10 раб. дней после отсечки")

            if amt:
                net = info.get("your_total_net", 0)
                lines.append(f"     Сумма: {amt} ₽/акц. × {shares} = <b>{fmt(net)} ₽</b> (после налога 13%)")
            elif "amount_per_share_min" in info:
                lines.append(f"     Прогноз: {info['amount_per_share_min']}–{info['amount_per_share_max']} ₽/акц.")
            if not is_confirmed and info.get("source"):
                lines.append(f"     <i>Источник: {info['source']}</i>")

    # 5. СКРИНЕР — топ-3
    screener = data.get("screener", {})
    cheap = screener.get("cheap_growth", [])
    if cheap:
        lines.append(f"\n─ ─ ─\n🔍 <b>Перспективные акции</b>")
        for s in cheap[:3]:
            div_mark = " 💰" if s.get("pays_dividends") else ""
            lines.append(
                f"  {s.get('grade','?')} <b>{s['ticker']}</b>{div_mark} "
                f"{fmt_price(s['price'])}  "
                f"+{s['pct']:.1f}%  score:{s['score']}"
            )
            div_next = s.get("dividend_next")
            if div_next:
                amt = div_next.get("amount_per_share")
                rd  = div_next.get("record_date")
                dtr = div_next.get("days_to_record")
                lines.append(f"     💰 Дивиденд {amt} ₽/акц., отсечка через {dtr} дн. ({rd})")

    # 6. АКТИВЫ
    assets = data.get("assets", [])
    if assets:
        lines.append(f"\n─ ─ ─\n🏦 <b>Активы (золото, нефть, серебро)</b>")
        for a in assets:
            if not a.get("price"):
                continue
            ch = a.get("pct", 0)
            arrow = "📈" if ch >= 0 else "📉"
            lines.append(
                f"  {arrow} <b>{a['name']}</b> ({a['ticker']}): "
                f"{fmt_price(a['price'])}  ({ch:+.1f}%)  {a.get('grade','?')}"
            )

    # 6.5. РАСТУЩИЙ ИНТЕРЕС
    rising = screener.get("rising_interest", [])
    if rising:
        lines.append(f"\n─ ─ ─\n🔭 <b>Растущий интерес</b>")
        for s in rising[:3]:
            vg = s.get("vol_growth", 0)
            sigs = " · ".join(s.get("signals", [])[:2])
            lines.append(f"  <b>{s['ticker']}</b> {s.get('name','')[:18]}")
            lines.append(f"     {fmt_price(s['price'])}  объём +{vg:.0f}% н/н  {sigs}")

    # 7. ТОП ПО ОБЪЁМУ (3 штуки)
    top_vol = screener.get("top_volume", [])
    if top_vol:
        lines.append(f"\n─ ─ ─\n🔥 <b>Топ по объёму</b>")
        for i, s in enumerate(top_vol[:3], 1):
            lines.append(
                f"  {i}. <b>{s['ticker']}</b>  {fmt_vol(s['volume'])}  "
                f"({s['pct']:+.1f}%)"
            )

    # 8. ИТОГ НЕДЕЛИ (только пятница)
    if WEEKDAY == 4:
        lines.append(f"\n─ ─ ─\n📅 <b>Пятница — итог недели</b>")
        lines.append("Полная недельная статистика в логах репозитория.")

    # Итоговый вывод
    fired  = data.get("rules_fired", [])
    psigs  = data.get("portfolio_signals", {})
    lines.append(build_conclusion(data, fired, psigs))

    # Ссылка на дашборд
    lines.append(
        f"\n─ ─ ─\n"
        f"📁 <a href='https://podlesnykhdn.github.io/my_prodject_invest/'>Дашборд</a>  |  "
        f"<a href='https://github.com/podlesnykhdn/my_prodject_invest/tree/main/logs'>Логи</a>"
    )

    return "\n".join(lines)

def build_alert(alert_id, data):
    """Формируем мгновенный алерт."""
    curr = data.get("currency", {})
    portfolio = data.get("portfolio", {})

    if alert_id == "GRADE_A_STOCK":
        cheap = data.get("screener", {}).get("cheap_growth", [])
        a_stocks = [s for s in cheap if s.get("score", 0) >= 75]
        if not a_stocks:
            return None
        s = a_stocks[0]
        return (
            f"🟢 <b>АЛЕРТ: Акция с оценкой A!</b>\n"
            f"<b>{s['ticker']}</b> — {fmt_price(s['price'])}\n"
            f"Score: {s['score']}/100  |  +{s['pct']:.1f}%\n"
            f"Объём: {fmt_vol(s['volume'])}"
        )

    if alert_id == "USD_THRESHOLD":
        usd_ch = curr.get("usd_change", 0)
        if abs(usd_ch) < 2.0:
            return None
        direction = "вырос" if usd_ch > 0 else "упал"
        usd_flag = "⚠️ данные из кэша!" if curr.get("is_cached") else ""
        return (
            f"💱 <b>АЛЕРТ: Доллар {direction} на {usd_ch:+.1f}%</b>\n"
            f"Курс: {curr.get('usd')} ₽  {usd_flag}\n"
            f"TGLD {'растёт' if usd_ch > 0 else 'под давлением'} | "
            f"Лента {'под давлением' if usd_ch > 0 else 'позитив'}"
        )

    if alert_id == "PORTFOLIO_DROP":
        tc = portfolio.get("total_change", 0)
        tv = portfolio.get("total_value", 0)
        tp = portfolio.get("total_pct", 0)
        if tp > -3.0:
            return None
        return (
            f"⚠️ <b>АЛЕРТ: Портфель упал на {tp:.1f}%</b>\n"
            f"Потери: {fmt_rub(tc)}\n"
            f"Стоимость: {tv:,.0f} ₽\n"
            f"Рекомендация: не паниковать, долгосрочная стратегия"
        )

    return None

# ─── ПРОВЕРКА АЛЕРТОВ ─────────────────────────────────────────────────────────

def check_alerts(data, log):
    if log.get("alerts_sent", 0) >= 5:
        print("Лимит алертов исчерпан (5/день)")
        return log

    alert_ids = ["GRADE_A_STOCK", "USD_THRESHOLD", "PORTFOLIO_DROP"]
    sent_ids  = [a["id"] for a in log.get("alerts", [])]

    for alert_id in alert_ids:
        if alert_id in sent_ids:
            continue
        msg = build_alert(alert_id, data)
        if msg:
            result = send(msg)
            if result.get("ok"):
                print(f"Алерт {alert_id} отправлен")
                log.setdefault("alerts", []).append({
                    "id":      alert_id,
                    "sent_at": datetime.now().strftime("%H:%M"),
                })
                log["alerts_sent"] = log.get("alerts_sent", 0) + 1
    return log

# ─── ОСНОВНАЯ ЛОГИКА ──────────────────────────────────────────────────────────

def run_morning():
    log  = load_log()
    data = load_collector_data()

    if log.get("morning_sent"):
        print(f"Утренняя сводка уже отправлена в {log.get('sent_at')}")
        # Всё равно проверяем алерты
        log = check_alerts(data, log)
        save_log(log)
        return

    msg = build_morning_report(data)
    result = send(msg)
    print(f"Утренняя сводка отправлена: {result.get('ok')}")

    log["morning_sent"] = True
    log["sent_at"] = datetime.now().strftime("%H:%M")
    log["date"] = TODAY

    # Проверяем алерты
    if data:
        log = check_alerts(data, log)

    save_log(log)

def run_alerts_only():
    """Проверка алертов без утренней сводки — запускается чаще."""
    log  = load_log()
    data = load_collector_data()
    if data:
        log = check_alerts(data, log)
        save_log(log)

def run_command():
    """Обработка команд пользователя."""
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=5"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        updates = json.loads(r.read()).get("result", [])

    data = load_collector_data()

    for update in updates[-5:]:
        msg  = update.get("message", {})
        text = (msg.get("text") or "").strip()
        cid  = str(msg.get("chat", {}).get("id", ""))

        if cid != str(CHAT_ID):
            continue

        if text == "/start":
            send(
                "👋 Привет! Я твой <b>Инвестиционный советник</b>.\n\n"
                "Каждое утро присылаю сводку по портфелю:\n"
                "• Стоимость в рублях и изменение за день\n"
                "• Макро: доллар, нефть, IMOEX\n"
                "• Сработавшие правила аналитики\n"
                "• Перспективные акции и активы\n\n"
                "<b>Команды:</b>\n"
                "/advice — полная сводка прямо сейчас\n"
                "/portfolio — только портфель\n"
                "/screener — перспективные акции\n"
                "/top — топ по объёму торгов"
            )

        elif text in ("/advice", "/start"):
            msg_text = build_morning_report(data)
            send(msg_text)

        elif text == "/portfolio":
            portfolio = data.get("portfolio", {}) if data else {}
            if not portfolio:
                send("⚠️ Данные по портфелю недоступны.")
                continue
            lines = [f"💼 <b>Портфель на {TODAY}</b>\n"]
            tv = portfolio.get("total_value", 0)
            tc = portfolio.get("total_change", 0)
            tp = portfolio.get("total_pct", 0)
            lines.append(f"Итого: <b>{tv:,.0f} ₽</b>  ({fmt_rub(tc)} / {tp:+.1f}%)\n")
            for pos in portfolio.get("positions", []):
                sig = "📈" if pos["change"] >= 0 else "📉"
                lines.append(
                    f"{sig} <b>{pos['ticker']}</b>: {fmt_price(pos['price'])}  "
                    f"× {pos['qty']} шт = {pos['value']:,.0f} ₽  "
                    f"({fmt_rub(pos['day_rub'])})"
                )
            send("\n".join(lines))

        elif text == "/screener":
            screener = data.get("screener", {}) if data else {}
            cheap = screener.get("cheap_growth", [])
            if not cheap:
                send("⚠️ Данные скринера недоступны. Биржа закрыта или нет данных.")
                continue
            lines = [f"🔍 <b>Перспективные акции (до 500 ₽)</b>\n"]
            for s in cheap[:8]:
                lines.append(
                    f"{s.get('grade','?')} <b>{s['ticker']}</b> — {s.get('name','')[:20]}\n"
                    f"   Цена: {fmt_price(s['price'])}  +{s['pct']:.1f}%  "
                    f"Score: {s['score']}/100\n"
                    f"   Объём: {fmt_vol(s['volume'])}"
                )
            send("\n".join(lines))

        elif text == "/top":
            screener = data.get("screener", {}) if data else {}
            top_vol = screener.get("top_volume", [])
            if not top_vol:
                send("⚠️ Данные по объёмам недоступны.")
                continue
            lines = [f"🔥 <b>Топ акций по объёму торгов</b>\n"]
            for i, s in enumerate(top_vol[:10], 1):
                lines.append(
                    f"{i}. <b>{s['ticker']}</b> — {s.get('name','')[:18]}\n"
                    f"   {fmt_vol(s['volume'])}  ({s['pct']:+.1f}%)"
                )
            send("\n".join(lines))

if __name__ == "__main__":
    print(f"Советник-бот запущен, режим: {MODE}")
    if MODE == "morning":
        run_morning()
    elif MODE == "alerts":
        run_alerts_only()
    elif MODE == "command":
        run_command()
    elif MODE == "auto":
        log = load_log()
        if not log.get("morning_sent"):
            run_morning()
        else:
            run_alerts_only()
