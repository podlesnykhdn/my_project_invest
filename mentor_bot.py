"""
mentor_bot.py — Telegram бот Ментора
Команды: /start, /lesson, /progress
Отправляет ежедневные уроки по инвестициям.
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path

TOKEN    = os.environ["MENTOR_BOT_TOKEN"]
CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs" / "mentor"
LESSONS_FILE = BASE_DIR / "lessons.json"
TODAY    = date.today().isoformat()
MODE     = os.environ.get("BOT_MODE", "morning")  # morning | command

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
    return {}

def save_log(data):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / f"{TODAY}.json"
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

def get_lesson_num():
    """Считаем номер урока по дням с начала проекта. Один урок в день, последовательно."""
    start = date(2025, 2, 28)  # первая покупка акций — начало инвестиционного пути
    delta = (date.today() - start).days + 1
    return max(delta, 1)

# ─── ЗАГРУЗКА УРОКА ───────────────────────────────────────────────────────────

def load_lesson(num):
    with open(LESSONS_FILE, encoding="utf-8") as f:
        lessons = json.load(f)
    idx = (num - 1) % len(lessons)
    return lessons[idx], num

# ─── ФОРМАТИРОВАНИЕ ───────────────────────────────────────────────────────────

def format_lesson(lesson, num):
    text = lesson["text"]
    # Конвертируем **bold** в HTML
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Параграфы
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "\n\n".join(paragraphs)

    return (
        f"🎓 <b>Ментор — Урок #{num}</b>\n"
        f"{'─' * 28}\n"
        f"<b>{lesson['title']}</b>\n\n"
        f"{body}\n\n"
        f"{'─' * 28}\n"
        f"📁 <a href='https://podlesnykhdn.github.io/my_project_invest/'>Дашборд портфеля</a>"
    )

def format_progress(num):
    total = 43  # общее количество уроков
    done  = min(num, total)
    pct   = int(done / total * 100)
    bar_len = 20
    filled = int(done / total * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    BLOCKS = [
        (1, 10, "Основы: акции, биржа, брокер"),
        (11, 20, "Анализ компаний: отчётность, мультипликаторы"),
        (21, 27, "Дивиденды: механика, налоги, стратегии"),
        (28, 35, "Макро: ставка ЦБ, нефть, инфляция, санкции"),
        (36, 43, "Стратегия: портфель, психология, когда продавать"),
    ]
    block_lines = []
    for start, end, title in BLOCKS:
        if done >= end:
            icon = "✅"
        elif done >= start:
            icon = "📍"
        else:
            icon = "⬜"
        block_lines.append(f"{icon} Уроки {start}-{end}: {title}")

    return (
        f"📊 <b>Твой прогресс обучения</b>\n"
        f"{'─' * 28}\n"
        f"Пройдено: {done}/{total} уроков\n"
        f"[{bar}] {pct}%\n\n"
        f"Текущий урок: #{num}\n\n"
        f"<b>Блоки:</b>\n" + "\n".join(block_lines)
    )

# ─── ОСНОВНАЯ ЛОГИКА ──────────────────────────────────────────────────────────


TERMS_FILE = BASE_DIR / "terms.json"
TERMS_LOG  = LOGS_DIR / "terms_progress.json"

def load_terms():
    with open(TERMS_FILE, encoding="utf-8") as f:
        return json.load(f)

def load_terms_progress():
    """Загружаем прогресс по терминам — какие уже показывали."""
    if TERMS_LOG.exists():
        with open(TERMS_LOG, encoding="utf-8") as f:
            return json.load(f)
    return {"shown": [], "confirmed": []}

def save_terms_progress(progress):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(TERMS_LOG, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def get_term_of_day(terms, progress):
    """Выбираем следующий термин — по порядку, не повторяя пока не пройдём все."""
    shown = progress.get("shown", [])
    # Если прошли все — начинаем сначала (повторение)
    if len(shown) >= len(terms):
        progress["shown"] = []
        shown = []
        save_terms_progress(progress)
    # Берём следующий непоказанный
    for i, term in enumerate(terms):
        if i not in shown:
            return term, i
    return terms[0], 0

def format_term(term):
    """Форматируем карточку термина."""
    import re
    full = term["full"]
    full = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", full)
    # Параграфы
    paragraphs = [p.strip() for p in full.split("\n") if p.strip()]
    body = "\n".join(paragraphs)

    return (
        f"{term['emoji']} <b>Термин дня: {term['term']}</b>\n"
        f"{'─' * 28}\n"
        f"<i>{term['short']}</i>\n\n"
        f"{body}\n\n"
        f"{'─' * 28}\n"
        f"📌 <b>Связь с твоим портфелем:</b>\n"
        f"{term.get('связь_с_портфелем', '')}\n\n"
        f"<i>Напиши /understood если усвоил этот термин — перейдём к следующему.</i>"
    )

def run_term_of_day():
    """Отправляем термин дня — второе сообщение от Ментора."""
    log = load_log()

    if log.get("term_sent"):
        print(f"Термин сегодня уже отправлен")
        return

    terms    = load_terms()
    progress = load_terms_progress()
    term, idx = get_term_of_day(terms, progress)

    msg = format_term(term)
    result = send(msg)
    print(f"Термин '{term['term']}' отправлен: {result.get('ok')}")

    # Сохраняем в лог
    log["term_sent"]  = True
    log["term_index"] = idx
    log["term_name"]  = term["term"]
    save_log(log)

    # Отмечаем термин как показанный
    progress.setdefault("shown", []).append(idx)
    save_terms_progress(progress)


def run_morning():
    """Утренняя отправка урока."""
    log = load_log()

    # Лимит: 1 урок в день
    if log.get("morning_sent"):
        print(f"Урок сегодня уже отправлен в {log.get('sent_at')}")
        return

    num = get_lesson_num()
    lesson, num = load_lesson(num)
    msg = format_lesson(lesson, num)

    try:
        result = send(msg)
        print(f"Урок #{num} отправлен: {result.get('ok')}")
    except Exception as e:
        print(f"[ERROR] send урок: {e}")
        result = {}

    save_log({
        "date":         TODAY,
        "lesson_num":   num,
        "lesson_title": lesson["title"],
        "morning_sent": True,
        "sent_at":      (lambda u, m: f"{m.strftime('%H:%M')} МСК ({u.strftime('%H:%M')} UTC)")(datetime.utcnow(), datetime.utcnow() + __import__("datetime").timedelta(hours=3)),
        "message_id":   result.get("result", {}).get("message_id"),
    })

    # Отправляем термин дня через 1 час (второе сообщение)
    run_term_of_day()

def run_command():
    """Обработка входящих команд."""

    # Диагностика: кто этот бот
    try:
        me_req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/getMe")
        with urllib.request.urlopen(me_req, timeout=10) as r:
            me = json.loads(r.read())
        print(f"Бот: {me.get('result', {})}")
    except Exception as e:
        print(f"[ERROR] getMe: {e}")

    print(f"CHAT_ID из секрета: {CHAT_ID}")

    # Диагностика: проверяем webhook
    try:
        wh_req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo")
        with urllib.request.urlopen(wh_req, timeout=10) as r:
            wh = json.loads(r.read())
        print(f"Webhook info: {wh.get('result', {})}")
        webhook_url = wh.get("result", {}).get("url", "")
        if webhook_url:
            print(f"[WARN] Установлен webhook: {webhook_url} — удаляю, чтобы getUpdates заработал")
            del_req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/deleteWebhook")
            with urllib.request.urlopen(del_req, timeout=10) as r:
                print(f"deleteWebhook result: {json.loads(r.read())}")
    except Exception as e:
        print(f"[ERROR] webhook check: {e}")

    # Пробуем getUpdates с offset=-1 чтобы захватить любые ожидающие
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=5&limit=100"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
    except Exception as e:
        print(f"[ERROR] getUpdates failed: {e}")
        return

    if not result.get("ok"):
        print(f"[ERROR] Telegram returned: {result}")
        return

    updates = result.get("result", [])
    print(f"Получено обновлений: {len(updates)}")

    last_update_id = None
    for u in updates:
        last_update_id = u.get("update_id")
        m = u.get("message") or {}
        text = m.get("text", "")
        cid  = (m.get("chat") or {}).get("id")
        print(f"  update_id={u.get('update_id')} text={text!r} chat_id={cid}")

    for update in updates:
        msg  = update.get("message") or {}
        text = msg.get("text", "")
        cid  = str((msg.get("chat") or {}).get("id", ""))

        if not text or cid != str(CHAT_ID):
            continue

        print(f"Обрабатываю команду: {text!r}")

        if text == "/start":
            send(
                "👋 Привет! Я твой <b>Ментор по инвестициям</b>.\n\n"
                "Каждое утро присылаю:\n"
                "• Урок об инвестициях\n"
                "• Термин дня — ключевое понятие с примером\n\n"
                "<b>Команды:</b>\n"
                "/lesson — получить урок прямо сейчас\n"
                "/progress — прогресс обучения\n"
                "/terms — все термины и прогресс\n"
                "/understood — отметить термин как усвоенный"
            )

        elif text == "/lesson":
            log = load_log()
            if log.get("morning_sent"):
                # Уже отправляли сегодня — отправим снова по запросу
                num = log.get("lesson_num", get_lesson_num())
            else:
                num = get_lesson_num()
            lesson, num = load_lesson(num)
            send(format_lesson(lesson, num))

        elif text == "/progress":
            num = get_lesson_num()
            send(format_progress(num))

        elif text.startswith("/catchup"):
            # /catchup 12 14 — присылает уроки с 12 по 14 включительно
            parts = text.split()
            if len(parts) == 3:
                try:
                    start, end = int(parts[1]), int(parts[2])
                    for n in range(start, end + 1):
                        lesson, num = load_lesson(n)
                        send(format_lesson(lesson, num))
                except ValueError:
                    send("Используй формат: /catchup 12 14")
            else:
                send("Используй формат: /catchup 12 14 — пришлю уроки с 12 по 14")

        elif text == "/understood":
            progress = load_terms_progress()
            confirmed = progress.get("confirmed", [])
            term_idx  = load_log().get("term_index")
            if term_idx is not None and term_idx not in confirmed:
                confirmed.append(term_idx)
                progress["confirmed"] = confirmed
                save_terms_progress(progress)
                terms = load_terms()
                total = len(terms)
                done  = len(confirmed)
                send(
                    f"✅ Отлично! Термин усвоен.\n\n"
                    f"Прогресс: {done}/{total} терминов\n"
                    f"{'█' * done}{'░' * (total-done)}\n\n"
                    f"Завтра изучим следующий!"
                )
            else:
                send("Термин уже отмечен как усвоенный! Завтра будет новый.")

        elif text == "/terms":
            progress = load_terms_progress()
            terms = load_terms()
            confirmed = progress.get("confirmed", [])
            done = len(confirmed)
            total = len(terms)
            pct = int(done/total*100)
            bar = "█" * done + "░" * (total-done)
            lines = [
                f"📚 <b>Словарь терминов</b>",
                f"Усвоено: {done}/{total} ({pct}%)",
                f"[{bar}]",
                f"",
                f"<b>Усвоенные термины:</b>"
            ]
            for i, term in enumerate(terms):
                icon = "✅" if i in confirmed else "⬜"
                lines.append(f"{icon} {term['emoji']} {term['term']}")
            send("\n".join(lines))

    # Подтверждаем обработку всех updates, чтобы они не приходили повторно
    if last_update_id is not None:
        try:
            confirm_url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_update_id+1}"
            urllib.request.urlopen(urllib.request.Request(confirm_url), timeout=10)
        except Exception as e:
            print(f"[WARN] offset confirm failed: {e}")


if __name__ == "__main__":
    print(f"Ментор-бот запущен, режим: {MODE}")
    try:
        if MODE == "morning":
            run_morning()
        elif MODE == "command":
            run_command()
        elif MODE == "alerts":
            run_term_of_day()
        elif MODE == "auto":
            log = load_log()
            if not log.get("morning_sent"):
                run_morning()
            elif not log.get("term_sent"):
                run_term_of_day()
            else:
                print("Урок и термин дня уже отправлены сегодня")
    except Exception as e:
        print(f"[ERROR] Ментор: {e}")
        import traceback
        traceback.print_exc()
