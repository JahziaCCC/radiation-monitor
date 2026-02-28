import os
import json
import hashlib
import datetime
import requests
import feedparser
from dateutil import tz
from deep_translator import GoogleTranslator

# =========================
# إعدادات تيليجرام
# =========================
BOT = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "mewa_state.json"
KSA_TZ = tz.gettz("Asia/Riyadh")

# =========================
# مصادر RSS (رسمية + إشارات مبكرة)
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
    "radiological emergency",
    "nuclear incident radiation",
    "IAEA alert radiation",
    "nuclear plant radioactive release",
]

def google_news_rss_url(q: str) -> str:
    from urllib.parse import quote
    qq = quote(q)
    return f"https://news.google.com/rss/search?q={qq}&hl=en&gl=US&ceid=US:en"

# =========================
# كلمات عامة للرصد (لدخول الخبر للفحص)
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
# استبعاد الضجيج (تنظيمي/إداري/غير حادث)
# =========================
NOISE_BLOCK = [
    # ضجيج عام
    "stock", "shares", "market", "crypto", "bitcoin",
    "movie", "game", "music", "festival", "nuclear family",

    # تنظيمي/إداري (سبب false positive السابق)
    "framework", "regulatory", "regulation", "rulemaking",
    "comment period", "public comment", "consultation",
    "policy", "guidance", "workshop", "public meeting",
    "kickstarts process", "creating regulatory framework",
    "licensing framework", "notice of proposed", "proposed rule",
    "request for information", "rfi",
    "commission meeting", "stakeholder", "press release",
    "strategic evacuation",  # غالباً سياق سياسي/إداري
]

# =========================
# A+ Smart: إشارات “إشعاع فعلي” (دليل لازم خصوصاً للـ Google News)
# =========================
RADIATION_EVIDENCE = [
    "radiation", "radioactive", "radiological",
    "radioactive release", "radiation release",
    "contamination", "dose", "dose rate", "sievert", "becquerel",
    "tritium", "iodine", "cesium", "caesium",
    "ines",
    # عربي
    "إشعاع", "مواد مشعة", "تلوث إشعاعي", "جرعة", "معدل الجرعة", "سيفرت", "بيكريل",
    "تريتيوم", "يود", "سيزيوم", "ines",
]

# =========================
# إشارات شدة عالية/متوسطة (لكن لا نعتمد على “إخلاء” وحده)
# =========================
SEVERITY_HIGH = [
    "state of emergency", "declared",
    "explosion", "fire", "meltdown", "core", "containment",
    "uncontrolled", "spike in radiation",
    "offsite dose", "dose rate",
    "ines 3", "ines 4", "ines 5", "ines 6", "ines 7",
    # عربي
    "إعلان حالة طوارئ", "انفجار", "حريق", "انصهار", "قلب المفاعل", "احتواء",
    "خارج السيطرة", "ارتفاع الإشعاع", "خارج الموقع", "معدل الجرعة",
    "مستوى ines",
]

SEVERITY_MED = [
    "leak", "spill", "shutdown", "scram", "incident",
    "investigation", "fault", "precaution",
    "minor release", "monitoring increased", "safety concern",
    # عربي
    "تسرب", "انسكاب", "إيقاف", "إيقاف طارئ", "حادث",
    "تحقيق", "عطل", "احترازي",
    "إطلاق طفيف", "رفع المراقبة", "مخاوف سلامة",
]

# “إخلاء” نعامله بحذر: لا يرفع الشدة إلا إذا معه دليل إشعاعي
EVAC_WORDS = ["evacuat", "shelter", "إخلاء", "إيواء"]

# إشارات قرب منطقي (ذكر دول/مواقع قريبة من المملكة)
NEAR_KSA_HINTS = [
    "saudi", "riyadh", "jeddah", "red sea", "gulf", "arabian gulf",
    "iran", "iraq", "kuwait", "qatar", "bahrain", "uae", "oman", "yemen",
    "jordan", "syria", "lebanon", "turkey", "egypt",
    # عربي
    "السعودية", "الرياض", "جدة", "البحر الأحمر", "الخليج", "الخليج العربي",
    "إيران", "العراق", "الكويت", "قطر", "البحرين", "الإمارات", "عُمان", "اليمن",
    "الأردن", "سوريا", "لبنان", "تركيا", "مصر",
]

# =========================
# إعدادات الرصد
# =========================
MAX_AGE_HOURS = 72
SUMMARY_HOURS = {6, 18}

# =========================
# وظائف مساعدة
# =========================
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

def translate_to_ar(text: str) -> str:
    """ترجمة عنوان الخبر للعربية (إذا فشل يرجع الأصل)."""
    try:
        return GoogleTranslator(source="auto", target="ar").translate(text)
    except Exception:
        return text

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

def has_radiation_evidence(title: str, summary: str) -> bool:
    blob = (title + " " + summary).lower()
    return any(w.lower() in blob for w in RADIATION_EVIDENCE)

def is_relevant(title: str, summary: str) -> bool:
    blob = (title + " " + summary).lower()
    if is_noise(title, summary):
        return False
    return any(k.lower() in blob for k in KEYWORDS)

def smart_assess(title: str, summary: str, source: str) -> dict:
    """
    تقييم A+ Smart:
    - شدة الخبر (عالي/متوسط/منخفض)
    - قرب منطقي
    - قرار تشغيلي (تأثير/جاهزية/مؤشر)
    قواعد مهمة:
    - كلمة “إخلاء” وحدها لا ترفع الشدة إلا إذا معها دليل إشعاعي واضح.
    """
    blob = (title + " " + summary).lower()
    reasons = []

    official = source.startswith("IAEA") or source.startswith("NRC")
    near = any(w.lower() in blob for w in NEAR_KSA_HINTS)
    evidence = has_radiation_evidence(title, summary)
    evac = any(w in blob for w in EVAC_WORDS)

    sev = 0  # 0 منخفض، 1 متوسط، 2 عالي

    # شدة عالية/متوسطة من كلمات قوية
    if any(w in blob for w in SEVERITY_HIGH):
        sev = 2
        reasons.append("إشارات شدة عالية (انفجار/حريق/مؤشر إشعاعي/INES)")
    elif any(w in blob for w in SEVERITY_MED):
        sev = 1
        reasons.append("إشارات شدة متوسطة (تسرب/إيقاف/تحقيق)")

    # الإخلاء: لا يكفي وحده
    if evac and not evidence:
        reasons.append("ذكر إخلاء بدون دليل إشعاعي (قد يكون سياق سياسي/أمني)")
        # لا نرفع الشدة بسبب الإخلاء لوحده
    elif evac and evidence:
        if sev < 1:
            sev = 1
        reasons.append("إخلاء مرتبط بمؤشر إشعاعي")

    if near:
        reasons.append("ذكر مواقع/دول قريبة من المملكة")
    if official:
        reasons.append("مصدر رسمي")
    if evidence:
        reasons.append("يوجد دليل إشعاعي صريح")

    # تحويل للتقييم التشغيلي
    if not evidence and source == "Google News":
        # تشدد على Google News: بدون دليل إشعاعي = أقل شيء
        impact = "غير متوقع"
        readiness = "مراقبة فقط"
        score = 15
        level = "🟢 منخفض"
        reasons = ["Google News بدون دليل إشعاعي صريح (تم تخفيض التقييم)"]

    else:
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

    return {"impact": impact, "readiness": readiness, "score": score, "level": level, "reasons": reasons}

def should_send_summary(state) -> bool:
    now = ksa_now()
    key = now.strftime("%Y%m%d%H")
    if now.hour in SUMMARY_HOURS and state.get("last_summary_ymdhr") != key:
        state["last_summary_ymdhr"] = key
        return True
    return False

# =========================
# التشغيل الرئيسي
# =========================
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
            link = (e.get("link") or "").strip()

            if not is_relevant(title, summary):
                continue

            t = parse_entry_time(e)
            if t and t < cutoff:
                continue

            guid = e.get("id") or e.get("guid") or link or (title + label)
            gid = sha1(label + "::" + guid)

            assess = smart_assess(title, summary, label)
            worst_score = max(worst_score, assess["score"])

            # Google News: لا نرسل تنبيه إذا ما فيه دليل إشعاعي واضح
            if label == "Google News" and not has_radiation_evidence(title, summary):
                # نسجلها في الحالة حتى ما تتكرر كل مرة، لكن بدون إرسال
                if gid not in seen:
                    seen[gid] = now.strftime("%Y-%m-%d %H:%M:%S")
                continue

            # جديد؟ أرسل
            if gid not in seen:
                seen[gid] = now.strftime("%Y-%m-%d %H:%M:%S")
                new_events.append((label, title, link, assess))

                title_ar = translate_to_ar(title)
                reasons = "؛ ".join(assess["reasons"][:3])

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
                    f"🇸🇦 الترجمة: {title_ar}\n"
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
                "• الرصد من IAEA + NRC + (Google News بشروط صارمة).\n"
            )
        else:
            top = new_events[:6]
            lines = "\n".join([f"• {s}: {t} | ترجمة: {translate_to_ar(t)}" for s, t, _, __ in top])
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
