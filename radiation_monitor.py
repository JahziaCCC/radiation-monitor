import os
import json
import hashlib
import datetime
import requests
import feedparser
from dateutil import tz

# =========================
# إعدادات تيليجرام
# =========================
BOT = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "mewa_state.json"
KSA_TZ = tz.gettz("Asia/Riyadh")

# =========================
# مصادر RSS (موثوقة + إشارات مبكرة)
# =========================
FEEDS = [
    "https://www-news.iaea.org/Feed.aspx",                # IAEA
    "https://www.nrc.gov/public-involve/rss?feed=event",  # NRC events
    "https://www.nrc.gov/public-involve/rss?feed=news",   # NRC news
]

# Google News RSS (إنذار مبكر بدون API)
GOOGLE_NEWS_QUERIES = [
    "radiation leak",
    "radioactive release",
    "nuclear incident",
    "radiological emergency",
    "IAEA alert",
    "reactor accident",
    "nuclear plant evacuation",
]

def google_news_rss_url(q: str) -> str:
    from urllib.parse import quote
    qq = quote(q)
    return f"https://news.google.com/rss/search?q={qq}&hl=en&gl=US&ceid=US:en"

# =========================
# فلتر الكلمات (لازم كلمة دالة واحدة على الأقل)
# =========================
KEYWORDS = [
    "radiation", "radioactive", "radiological", "nuclear",
    "leak", "release", "contamination", "evacuation",
    "incident", "emergency", "iaea", "ines", "nrc",
    "tritium", "iodine", "cesium", "caesium",
    "reactor", "plant", "power station",
    # عربي
    "إشعاع", "نووي", "تسرب", "مواد مشعة", "تلوث إشعاعي", "طوارئ إشعاعية",
    "مفاعل", "محطة نووية", "إخلاء",
]

# =========================
# استبعاد الضجيج (الأهم لتقليل التنبيهات الكاذبة)
# - أضفنا كلمات تنظيمية/إدارية حتى لا تُحسب كحادث
# =========================
NOISE_BLOCK = [
    # ضجيج عام
    "stock", "shares", "market", "crypto", "bitcoin",
    "movie", "game", "music", "festival",
    "nuclear family",

    # ضجيج تنظيمي/إداري (سبب تنبيهك السابق)
    "framework", "regulatory", "regulation", "rulemaking",
    "comment period", "public comment", "consultation",
    "policy", "guidance", "workshop", "public meeting",
    "kickstarts process", "creating regulatory framework",
    "licensing framework", "notice of proposed", "proposed rule",
    "request for information", "rfi",
    "commission meeting", "stakeholder", "press release",
]

# =========================
# إعدادات الرصد
# =========================
MAX_AGE_HOURS = 72          # نافذة الرصد
SUMMARY_HOURS = {6, 18}     # ملخص مرتين يوميًا بتوقيت السعودية

# =========================
# A+ Smart: إشارات "شدة" (حادث حقيقي غالباً)
# =========================
SEVERITY_HIGH = [
    "evacuat", "shelter", "state of emergency", "declared",
    "explosion", "fire", "meltdown", "core", "containment",
    "radioactive release", "radiation release", "contamination",
    "offsite dose", "dose rate", "sievert", "becquerel",
    "ines 3", "ines 4", "ines 5", "ines 6", "ines 7",
    "uncontrolled", "leak detected", "spike in radiation",

    # عربي
    "إخلاء", "إيواء", "طوارئ", "إعلان حالة طوارئ",
    "انفجار", "حريق", "انصهار", "قلب المفاعل", "احتواء",
    "إطلاق مواد مشعة", "انبعاث إشعاعي", "تلوث",
    "جرعة", "معدل الجرعة", "سيفرت", "بيكريل",
    "ارتفاع الإشعاع", "خارج السيطرة",
]

SEVERITY_MED = [
    "leak", "spill", "shutdown", "scram", "incident",
    "investigation", "fault", "tritium", "precaution",
    "minor release", "monitoring increased", "safety concern",

    # عربي
    "تسرب", "انسكاب", "إيقاف", "إيقاف طارئ", "حادث",
    "تحقيق", "عطل", "تريتيوم", "احترازي",
    "إطلاق طفيف", "رفع المراقبة", "مخاوف سلامة",
]

# إشارات "قرب منطقي" بدون خرائط (ذكر دول/مواقع قريبة من المملكة)
NEAR_KSA_HINTS = [
    "saudi", "riyadh", "jeddah", "red sea", "gulf", "arabian gulf",
    "iran", "iraq", "kuwait", "qatar", "bahrain", "uae", "oman", "yemen",
    "jordan", "syria", "lebanon", "turkey", "egypt",
    # عربي
    "السعودية", "الرياض", "جدة", "البحر الأحمر", "الخليج", "الخليج العربي",
    "إيران", "العراق", "الكويت", "قطر", "البحرين", "الإمارات", "عُمان", "اليمن",
    "الأردن", "سوريا", "لبنان", "تركيا", "مصر",
]

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def ksa_now() -> datetime.datetime:
    return datetime.datetime.now(tz=KSA_TZ)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}, "last_summary_ymdhr": ""}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def telegram_send(text: str):
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()

def parse_entry_time(entry):
    t = None
    if getattr(entry, "published_parsed", None):
        t = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc)
    elif getattr(entry, "updated_parsed", None):
        t = datetime.datetime(*entry.updated_parsed[:6], tzinfo=datetime.timezone.utc)
    return t

def source_label(url: str) -> str:
    if "www-news.iaea.org" in url:
        return "IAEA"
    if "nrc.gov" in url and "feed=event" in url:
        return "NRC (Event)"
    if "nrc.gov" in url and "feed=news" in url:
        return "NRC (News)"
    if "news.google.com" in url:
        return "Google News"
    return "RSS"

def is_noise(title: str, summary: str) -> bool:
    blob = (title + " " + summary).lower()
    return any(w in blob for w in NOISE_BLOCK)

def is_relevant(title: str, summary: str) -> bool:
    blob = (title + " " + summary).lower()
    if is_noise(title, summary):
        return False
    return any(k.lower() in blob for k in KEYWORDS)

def smart_assess(title: str, summary: str, source: str) -> dict:
    """
    تقييم A+ Smart:
    - يحدد شدة الخبر (عالي/متوسط/منخفض)
    - يحدد قرب منطقي (ذكر مواقع قريبة)
    - يطلع تأثير/جاهزية/مؤشر خطر
    """
    blob = (title + " " + summary).lower()

    reasons = []
    sev = 0  # 0 منخفض، 1 متوسط، 2 عالي

    if any(w in blob for w in SEVERITY_HIGH):
        sev = 2
        reasons.append("إشارات شدة عالية (طوارئ/إخلاء/إطلاق مواد مشعة/INES)")
    elif any(w in blob for w in SEVERITY_MED):
        sev = 1
        reasons.append("إشارات شدة متوسطة (تسرب/إيقاف/تحقيق)")

    near = any(w.lower() in blob for w in NEAR_KSA_HINTS)
    if near:
        reasons.append("ذكر مواقع/دول قريبة من المملكة")

    official = source.startswith("IAEA") or source.startswith("NRC")
    if official:
        reasons.append("مصدر رسمي")

    # تحويل التقييم إلى قرار تشغيلي
    if sev == 0 and not near:
        impact = "غير متوقع"
        readiness = "مراقبة فقط"
        score = 15
        level = "🟢 منخفض"
    elif sev == 0 and near:
        impact = "منخفض"
        readiness = "متابعة"
        score = 30
        level = "🟠 متوسط"
    elif sev == 1 and not near:
        impact = "منخفض"
        readiness = "متابعة"
        score = 40
        level = "🟠 متوسط"
    elif sev == 1 and near:
        impact = "متوسط"
        readiness = "متابعة عاجلة"
        score = 60
        level = "🔴 مرتفع"
    else:  # sev == 2
        impact = "مرتفع" if near else "متوسط"
        readiness = "تصعيد فوري" if near else "متابعة عاجلة"
        score = 80 if near else 65
        level = "🔴 مرتفع"

    if not reasons:
        reasons = ["لا توجد مؤشرات شدة/قرب واضحة"]

    return {
        "impact": impact,
        "readiness": readiness,
        "score": score,
        "level": level,
        "reasons": reasons
    }

def should_send_summary(state) -> bool:
    now = ksa_now()
    key = now.strftime("%Y%m%d%H")
    if now.hour in SUMMARY_HOURS and state.get("last_summary_ymdhr") != key:
        state["last_summary_ymdhr"] = key
        return True
    return False

def main():
    state = load_state()
    seen = state.get("seen", {})

    now = ksa_now()
    cutoff = now.astimezone(datetime.timezone.utc) - datetime.timedelta(hours=MAX_AGE_HOURS)

    urls = list(FEEDS) + [google_news_rss_url(q) for q in GOOGLE_NEWS_QUERIES]

    new_events = []
    worst_score = 15

    for url in urls:
        feed = feedparser.parse(url)
        label = source_label(url)

        for e in feed.entries[:40]:
            title = (e.get("title") or "").strip()
            summary = (e.get("summary") or "").strip()

            # فلترة أولية
            if not is_relevant(title, summary):
                continue

            # وقت الخبر
            t = parse_entry_time(e)
            if t and t < cutoff:
                continue

            link = (e.get("link") or "").strip()

            # معرف فريد
            guid = e.get("id") or e.get("guid") or link or (title + label)
            gid = sha1(label + "::" + guid)

            # تقييم تشغيلي
            assess = smart_assess(title, summary, label)
            worst_score = max(worst_score, assess["score"])

            # جديد؟
            if gid not in seen:
                seen[gid] = now.strftime("%Y-%m-%d %H:%M:%S")
                new_events.append((label, title, link, assess))

                # تنبيه فوري
                reasons = "؛ ".join(assess["reasons"][:2])
                msg = (
                    "☢️ تنبيه إشعاعي/نووي (A+)\n"
                    f"🕒 {now.strftime('%Y-%m-%d %H:%M')} KSA\n"
                    "════════════════════\n"
                    "🌍 التقييم السريع:\n"
                    f"• التأثير على المملكة: {assess['impact']}\n"
                    f"• مستوى الجاهزية: {assess['readiness']}\n"
                    f"• مستوى الخطورة: {assess['level']} ({assess['score']}/100)\n"
                    f"• السبب: {reasons}\n"
                    "════════════════════\n"
                    f"📌 المصدر: {label}\n"
                    f"📰 {title}\n"
                    f"🔗 {link}\n"
                )
                telegram_send(msg)

    state["seen"] = seen

    # ===== الملخص التنفيذي (مرتين يومياً) =====
    if should_send_summary(state):
        if worst_score < 30:
            level = "🟢 منخفض"
            impact = "غير متوقع"
            readiness = "مراقبة فقط"
        elif worst_score < 60:
            level = "🟠 متوسط"
            impact = "منخفض"
            readiness = "متابعة"
        elif worst_score < 75:
            level = "🔴 مرتفع"
            impact = "متوسط"
            readiness = "متابعة عاجلة"
        else:
            level = "🔴 مرتفع"
            impact = "مرتفع"
            readiness = "تصعيد فوري"

        if not new_events:
            summary = (
                "☢️ تقرير الرصد الإشعاعي – غرفة العمليات (A+)\n"
                f"🕒 {now.strftime('%Y-%m-%d %H:%M')} KSA\n\n"
                "════════════════════\n"
                f"📊 مؤشر الخطر الإشعاعي:\n{worst_score} / 100 — {level}\n\n"
                "════════════════════\n"
                "🌍 التقييم التشغيلي:\n"
                f"• التأثير المحتمل على المملكة: {impact}\n"
                f"• مستوى الجاهزية: {readiness}\n\n"
                "════════════════════\n"
                f"📍 الملخص التنفيذي:\n"
                f"• لا توجد إشارات جديدة مطابقة خلال آخر {MAX_AGE_HOURS} ساعة.\n"
                "• الرصد من IAEA + NRC + إشارات إعلامية مبكرة.\n"
            )
        else:
            top = new_events[:6]
            lines = "\n".join([f"• {s}: {t}" for s, t, _, __ in top])
            summary = (
                "☢️ تقرير الرصد الإشعاعي – غرفة العمليات (A+)\n"
                f"🕒 {now.strftime('%Y-%m-%d %H:%M')} KSA\n\n"
                "════════════════════\n"
                f"📊 مؤشر الخطر الإشعاعي:\n{worst_score} / 100 — {level}\n\n"
                "════════════════════\n"
                "🌍 التقييم التشغيلي:\n"
                f"• التأثير المحتمل على المملكة: {impact}\n"
                f"• مستوى الجاهزية: {readiness}\n\n"
                "════════════════════\n"
                "📌 أبرز الإشارات الجديدة:\n"
                f"{lines}\n"
            )
        telegram_send(summary)

    save_state(state)

if __name__ == "__main__":
    main()
