import os
import smtplib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import urllib.request

GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
EMAIL = "podlesnykhdn@gmail.com"
lesson_num = int(os.environ.get("LESSON_NUM", "1"))
today = datetime.now().strftime("%d.%m.%Y")

with open("lessons.json", encoding="utf-8") as f:
    lessons = json.load(f)
idx = (lesson_num - 1) % len(lessons)
lesson = lessons[idx]

RSS_FEEDS = [
    ("RBC", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    ("Interfax", "https://www.interfax.ru/rss.asp"),
    ("Vedomosti", "https://www.vedomosti.ru/rss/news"),
    ("Kommersant", "https://www.kommersant.ru/RSS/news.xml"),
    ("Finam", "https://www.finam.ru/analysis/newsitem/rss/"),
    ("BKS", "https://bcs-express.ru/rss"),
    ("SmartLab", "https://smart-lab.ru/blog/rss/"),
]

POSITIONS = {
    "X5 / Korporativny centr": ["x5", "pyaterochka", "perekrestok", "chizhik", "five",
                                  "пятёрочка", "пятерочка", "перекрёсток", "перекресток", "чижик"],
    "Lenta": ["лента", "lent"],
    "Sberbank": ["сбербанк", "сбер", "sber", "греф"],
    "Novabev Group": ["novabev", "новабев", "белуга", "belu"],
    "Zoloto / TGLD": ["золото", "gold", "tgld"],
}

GENERAL_KW = ["московская биржа", "фондовый рынок", "акци", "дивиденд", "ключевая ставка", "цб рф", "ритейл"]

def fetch_rss(url, source_name):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            root = ET.fromstring(resp.read())
        items = []
        for item in root.findall(".//item")[:50]:
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el is None:
                continue
            title = (title_el.text or "").strip()
            link = (link_el.text or "").strip() if link_el is not None else ""
            items.append({"source": source_name, "title": title, "link": link})
        return items
    except Exception as e:
        print(f"RSS error {source_name}: {e}")
        return []

all_items = []
for sname, url in RSS_FEEDS:
    all_items.extend(fetch_rss(url, sname))

position_news = {pos: [] for pos in POSITIONS}
general_news = []
smartlab_opinions = []

for item in all_items:
    text = item["title"].lower()
    matched = False
    for pos_name, keywords in POSITIONS.items():
        if any(k in text for k in keywords):
            if len(position_news[pos_name]) < 3:
                position_news[pos_name].append(item)
            matched = True
            break
    if not matched:
        if any(k in text for k in GENERAL_KW) and len(general_news) < 4:
            general_news.append(item)
    if item["source"] == "SmartLab" and any(k in text for k in ["акци","портфел","дивид","инвест","сбер","x5","лент","белуг","золот"]):
        if len(smartlab_opinions) < 4:
            smartlab_opinions.append(item)

def news_link(item):
    t = item["title"].replace("<","&lt;").replace(">","&gt;")
    return '<p style="margin:5px 0;font-size:13px;">- <a href="{}" style="color:#2563eb;text-decoration:none;">{}</a> <span style="color:#9ca3af;font-size:11px;">({})</span></p>'.format(item["link"], t, item["source"])

news_sections = ""
pos_labels = {
    "X5 / Korporativny centr": "X5 / Корпоративный центр",
    "Lenta": "Лента",
    "Sberbank": "Сбербанк",
    "Novabev Group": "Novabev Group",
    "Zoloto / TGLD": "Золото / TGLD",
}
for pos_name, items in position_news.items():
    if items:
        label = pos_labels.get(pos_name, pos_name)
        news_sections += '<p style="margin:12px 0 4px;font-size:12px;font-weight:bold;color:#1e3a5f;">** {}**</p>'.format(label)
        news_sections += "".join(news_link(i) for i in items)

if general_news:
    news_sections += '<p style="margin:12px 0 4px;font-size:12px;font-weight:bold;color:#1e3a5f;">Рынок и экономика</p>'
    news_sections += "".join(news_link(i) for i in general_news)

if not news_sections:
    news_sections = '<p style="color:#9ca3af;font-size:13px;">Новостей по позициям портфеля сегодня не найдено.</p>'

opinion_block = ""
if smartlab_opinions:
    opinion_block = '<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">'
    opinion_block += '<p style="color:#7c3aed;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:1px;margin:0 0 8px;">Мнения инвесторов (Smart-Lab)</p>'
    opinion_block += "".join(news_link(i) for i in smartlab_opinions)

def to_html(text):
    lines = text.strip().split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            result.append("<br>")
        elif line.startswith("**") and line.endswith("**"):
            result.append('<p style="margin:8px 0;"><strong style="color:#1e3a5f;">{}</strong></p>'.format(line.strip("*")))
        else:
            result.append('<p style="margin:6px 0;color:#374151;">{}</p>'.format(line))
    return "\n".join(result)

html_body = (
    '<!DOCTYPE html><html><head><meta charset="UTF-8"></head>'
    '<body style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;background:#f9fafb;padding:20px;">'
    '<div style="background:#1e3a5f;padding:20px 24px;border-radius:10px 10px 0 0;">'
    '<h1 style="color:#fff;margin:0;font-size:18px;">my_prodject_invest</h1>'
    '<p style="color:#93c5fd;margin:4px 0 0;font-size:13px;">' + today + ' · Ежедневная рассылка</p>'
    '</div>'
    '<div style="background:#fff;padding:24px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">'
    '<div style="border-left:4px solid #2563eb;padding-left:16px;margin-bottom:28px;">'
    '<p style="color:#2563eb;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:1px;margin:0 0 4px;">Ментор - Урок №' + str(lesson_num) + '</p>'
    '<h2 style="font-size:16px;color:#1e3a5f;margin:0 0 12px;">' + lesson["title"] + '</h2>'
    + to_html(lesson["text"]) +
    '</div>'
    '<hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">'
    '<div style="border-left:4px solid #059669;padding-left:16px;">'
    '<p style="color:#059669;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:1px;margin:0 0 12px;">Аналитик - Новости по портфелю</p>'
    + news_sections + opinion_block +
    '<div style="margin-top:16px;padding:12px;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0;">'
    '<p style="margin:0;font-size:12px;color:#166534;"><strong>Портфель:</strong> X5 (42) · Лента (53) · Сбербанк (74) · Novabev (12) · TGLD (4618) · ~280 000 руб.</p>'
    '</div></div></div>'
    '<div style="background:#f3f4f6;padding:14px 24px;border-radius:0 0 10px 10px;border:1px solid #e5e7eb;border-top:none;">'
    '<p style="color:#9ca3af;font-size:11px;margin:0;text-align:center;">my_prodject_invest · github.com/podlesnykhdn/my_prodject_invest</p>'
    '</div></body></html>'
)

print("Отправляю письмо...")
msg = MIMEMultipart("alternative")
msg["Subject"] = "Урок #{}: {} · {}".format(lesson_num, lesson["title"], today)
msg["From"] = EMAIL
msg["To"] = EMAIL
msg.attach(MIMEText(html_body, "html", "utf-8"))

with smtplib.SMTP("smtp.gmail.com", 587) as server:
    server.ehlo()
    server.starttls()
    server.login(EMAIL, GMAIL_PASSWORD)
    server.sendmail(EMAIL, EMAIL, msg.as_string())
    print("Письмо отправлено!")
