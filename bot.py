import os
import csv
import json
import asyncio
import re
import io
import logging
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# লগিং সেটআপ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
# Render এনভায়রনমেন্ট ভেরিয়েবল থেকে নিন
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID   = int(os.environ.get("OWNER_ID", "0"))   # Your Telegram user id
DATA_FILE  = "data.json"

# ─── STATES ──────────────────────────────────────────────────────────────────
(
    AWAIT_CSV, AWAIT_CHANNEL_ID, AWAIT_PRE_MSG, AWAIT_MARKER_NAME,
    AWAIT_MARKER_VALUE, AWAIT_EXPL_TAG, AWAIT_Q_TAG, AWAIT_ACCESS_USER,
    AWAIT_REVOKE_USER, AWAIT_SELECT_MARKER
) = range(10)

# ─── DATA HELPERS ─────────────────────────────────────────────────────────────
def load_data() -> dict:
    """JSON ফাইল থেকে ডাটা লোড করুন"""
    default = {
        "authorized": [],          # extra authorized user ids
        "channels": {},            # {"alias": channel_id}
        "questions": [],           # parsed CSV rows
        "pre_message": "",         # message sent before quiz
        "markers": {},             # {"name": "value"}
        "active_marker": None,
        "q_tag": "",               # appended to every question text
        "expl_tag": "",            # appended to every explanation
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default.update(saved)
        except Exception as e:
            logger.error(f"Data load error: {e}")
    return default

def save_data(data: dict):
    """ডাটা JSON ফাইলে সেভ করুন"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def is_authorized(user_id: int, data: dict) -> bool:
    return user_id == OWNER_ID or user_id in data.get("authorized", [])

async def auth_check(update: Update, data: dict) -> bool:
    if not is_authorized(update.effective_user.id, data):
        await update.effective_message.reply_text("⛔ আপনার এক্সেস নেই।")
        return False
    return True

# ─── CSV PARSER ──────────────────────────────────────────────────────────────
def parse_csv(content: str) -> list[dict]:
    """CSV কন্টেন্ট পার্স করুন"""
    questions = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        q = {
            "question":    row.get("questions", "").strip(),
            "options":     [],
            "answer":      row.get("answer", "").strip(),
            "explanation": row.get("explanation", "").strip(),
            "type":        row.get("type", "1").strip(),
            "section":     row.get("section", "1").strip(),
        }
        for i in range(1, 6):
            opt = row.get(f"option{i}", "").strip()
            if opt:
                q["options"].append(opt)
        if q["question"] and q["options"] and q["answer"]:
            questions.append(q)
    return questions

def get_correct_index(question: dict) -> int:
    """Return 0-based index of correct answer."""
    ans = question["answer"].strip()
    # Try numeric (1-based)
    if ans.isdigit():
        idx = int(ans) - 1
        if 0 <= idx < len(question["options"]):
            return idx
    # Try matching option text
    for i, opt in enumerate(question["options"]):
        if opt.strip().lower() == ans.lower():
            return i
    return 0

# ─── COMMAND: /start ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return
    text = (
        "🤖 *Quiz Bot — কমান্ড লিস্ট*\n\n"
        "📂 *CSV*\n"
        "`/upload_csv` — CSV ফাইল আপলোড করুন\n\n"
        "📡 *চ্যানেল ম্যানেজমেন্ট*\n"
        "`/add_channel` — চ্যানেল যোগ করুন\n"
        "`/list_channels` — চ্যানেল তালিকা\n\n"
        "🚀 *কুইজ পাঠানো*\n"
        "`/send_quiz` — কুইজ পাঠান\n"
        "`/set_pre_message` — কুইজের আগের মেসেজ সেট করুন\n\n"
        "🏷️ *মার্কার ও ট্যাগ*\n"
        "`/add_marker` — নতুন মার্কার সেভ করুন\n"
        "`/list_markers` — মার্কার তালিকা\n"
        "`/use_marker` — মার্কার সিলেক্ট করুন\n"
        "`/no_marker` — মার্কার বন্ধ করুন\n"
        "`/set_q_tag` — প্রশ্নের শেষে ট্যাগ\n"
        "`/set_expl_tag` — ব্যাখ্যার শেষে ট্যাগ\n\n"
        "👥 *এক্সেস কন্ট্রোল*\n"
        "`/grant_access <user_id>` — এক্সেস দিন\n"
        "`/revoke_access <user_id>` — এক্সেস নিন\n"
        "`/list_access` — এক্সেস তালিকা\n\n"
        "`/status` — বর্তমান সেটিংস\n\n"
        "`/cancel` — যেকোনো অপারেশন বাতিল করুন"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── CSV UPLOAD ───────────────────────────────────────────────────────────────
async def cmd_upload_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    await update.message.reply_text("📎 CSV ফাইলটি পাঠান (`.csv` ফরম্যাটে):")
    return AWAIT_CSV

async def receive_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".csv"):
        await update.message.reply_text("❌ .csv ফাইল পাঠান।")
        return AWAIT_CSV
    file = await doc.get_file()
    content_bytes = await file.download_as_bytearray()
    content = content_bytes.decode("utf-8-sig", errors="replace")
    questions = parse_csv(content)
    if not questions:
        await update.message.reply_text("❌ CSV পার্স করা যায়নি। ফরম্যাট চেক করুন।")
        return ConversationHandler.END
    data["questions"] = questions
    save_data(data)
    await update.message.reply_text(f"✅ {len(questions)} টি প্রশ্ন লোড হয়েছে!")
    return ConversationHandler.END

# ─── ADD CHANNEL ──────────────────────────────────────────────────────────────
async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    await update.message.reply_text(
        "চ্যানেল ID পাঠান (যেমন: `-1001234567890`):\n"
        "_(বট অবশ্যই চ্যানেলে Admin হতে হবে)_",
        parse_mode="Markdown"
    )
    return AWAIT_CHANNEL_ID

async def receive_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    ch_id = update.message.text.strip()
    # Try to get chat info for alias
    try:
        chat = await context.bot.get_chat(ch_id)
        alias = chat.title or ch_id
    except Exception:
        alias = ch_id
    data["channels"][alias] = ch_id
    save_data(data)
    await update.message.reply_text(f"✅ চ্যানেল যোগ হয়েছে: *{alias}*", parse_mode="Markdown")
    return ConversationHandler.END

async def cmd_list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return
    if not data["channels"]:
        await update.message.reply_text("কোনো চ্যানেল নেই।")
        return
    lines = [f"• `{alias}` → `{cid}`" for alias, cid in data["channels"].items()]
    await update.message.reply_text("📡 *চ্যানেল তালিকা:*\n" + "\n".join(lines), parse_mode="Markdown")

# ─── PRE-MESSAGE ──────────────────────────────────────────────────────────────
async def cmd_set_pre_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    cur = data.get("pre_message", "")
    await update.message.reply_text(
        f"বর্তমান মেসেজ:\n`{cur or 'কোনোটি নেই'}`\n\nনতুন মেসেজ পাঠান (মুছতে `.` পাঠান):",
        parse_mode="Markdown"
    )
    return AWAIT_PRE_MSG

async def receive_pre_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    val = update.message.text.strip()
    data["pre_message"] = "" if val == "." else val
    save_data(data)
    await update.message.reply_text("✅ প্রি-মেসেজ সেট হয়েছে।")
    return ConversationHandler.END

# ─── MARKERS ─────────────────────────────────────────────────────────────────
async def cmd_add_marker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    await update.message.reply_text("মার্কারের নাম দিন (যেমন: `MAT2425`):", parse_mode="Markdown")
    return AWAIT_MARKER_NAME

async def receive_marker_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["marker_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"মার্কারের মান দিন (যেমন: `MAT 24-25`):\n"
        f"প্রশ্নে এভাবে দেখাবে: [MAT 24-25]",
        parse_mode="Markdown"
    )
    return AWAIT_MARKER_VALUE

async def receive_marker_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    name = context.user_data.get("marker_name", "unnamed")
    value = update.message.text.strip()
    data["markers"][name] = value
    save_data(data)
    await update.message.reply_text(f"✅ মার্কার সেভ হয়েছে: `{name}` → `[{value}]`", parse_mode="Markdown")
    return ConversationHandler.END

async def cmd_list_markers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return
    if not data["markers"]:
        await update.message.reply_text("কোনো মার্কার নেই।")
        return
    active = data.get("active_marker")
    lines = []
    for name, val in data["markers"].items():
        star = " ✅ (সক্রিয়)" if name == active else ""
        lines.append(f"• `{name}` → `[{val}]`{star}")
    await update.message.reply_text("🏷️ *মার্কার তালিকা:*\n" + "\n".join(lines), parse_mode="Markdown")

async def cmd_use_marker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    if not data["markers"]:
        await update.message.reply_text("কোনো মার্কার নেই। /add_marker দিয়ে যোগ করুন।")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(f"{name} → [{val}]", callback_data=f"marker:{name}")]
        for name, val in data["markers"].items()
    ]
    await update.message.reply_text(
        "কোন মার্কার ব্যবহার করবেন?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return AWAIT_SELECT_MARKER

async def cb_select_marker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    name = query.data.split(":", 1)[1]
    data["active_marker"] = name
    save_data(data)
    val = data["markers"].get(name, "")
    await query.edit_message_text(f"✅ সক্রিয় মার্কার: `{name}` → `[{val}]`", parse_mode="Markdown")
    return ConversationHandler.END

async def cmd_no_marker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return
    data["active_marker"] = None
    save_data(data)
    await update.message.reply_text("✅ মার্কার বন্ধ করা হয়েছে।")

# ─── TAGS ─────────────────────────────────────────────────────────────────────
async def cmd_set_q_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    cur = data.get("q_tag", "")
    await update.message.reply_text(
        f"বর্তমান প্রশ্ন-ট্যাগ: `{cur or 'কোনোটি নেই'}`\nনতুন ট্যাগ পাঠান (মুছতে `.` পাঠান):",
        parse_mode="Markdown"
    )
    return AWAIT_Q_TAG

async def receive_q_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    val = update.message.text.strip()
    data["q_tag"] = "" if val == "." else val
    save_data(data)
    await update.message.reply_text("✅ প্রশ্ন-ট্যাগ আপডেট হয়েছে।")
    return ConversationHandler.END

async def cmd_set_expl_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return ConversationHandler.END
    cur = data.get("expl_tag", "")
    await update.message.reply_text(
        f"বর্তমান ব্যাখ্যা-ট্যাগ: `{cur or 'কোনোটি নেই'}`\nনতুন ট্যাগ পাঠান (মুছতে `.` পাঠান):",
        parse_mode="Markdown"
    )
    return AWAIT_EXPL_TAG

async def receive_expl_tag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    val = update.message.text.strip()
    data["expl_tag"] = "" if val == "." else val
    save_data(data)
    await update.message.reply_text("✅ ব্যাখ্যা-ট্যাগ আপডেট হয়েছে।")
    return ConversationHandler.END

# ─── ACCESS CONTROL ──────────────────────────────────────────────────────────
async def cmd_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ শুধুমাত্র মালিক এক্সেস দিতে পারবেন।")
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/grant_access <user_id>`", parse_mode="Markdown")
        return
    data = load_data()
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ সঠিক ইউজার আইডি দিন (শুধু সংখ্যা)")
        return
    if uid not in data["authorized"]:
        data["authorized"].append(uid)
        save_data(data)
    await update.message.reply_text(f"✅ User `{uid}` কে এক্সেস দেওয়া হয়েছে।", parse_mode="Markdown")

async def cmd_revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ শুধুমাত্র মালিক এক্সেস নিতে পারবেন।")
        return
    if not context.args:
        await update.message.reply_text("ব্যবহার: `/revoke_access <user_id>`", parse_mode="Markdown")
        return
    data = load_data()
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ সঠিক ইউজার আইডি দিন (শুধু সংখ্যা)")
        return
    if uid in data["authorized"]:
        data["authorized"].remove(uid)
        save_data(data)
    await update.message.reply_text(f"✅ User `{uid}` এর এক্সেস নেওয়া হয়েছে।", parse_mode="Markdown")

async def cmd_list_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ শুধুমাত্র মালিক দেখতে পারবেন।")
        return
    data = load_data()
    ids = data.get("authorized", [])
    if not ids:
        await update.message.reply_text("কোনো অতিরিক্ত authorized user নেই।")
    else:
        await update.message.reply_text("👥 *Authorized Users:*\n" + "\n".join(f"• `{i}`" for i in ids), parse_mode="Markdown")

# ─── STATUS ──────────────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return
    marker_name = data.get("active_marker")
    marker_val = f"[{data['markers'][marker_name]}]" if marker_name and marker_name in data["markers"] else "নেই"
    text = (
        f"📊 *বর্তমান সেটিংস*\n\n"
        f"🗂️ প্রশ্ন সংখ্যা: `{len(data['questions'])}`\n"
        f"📡 চ্যানেল: `{len(data['channels'])}`\n"
        f"💬 প্রি-মেসেজ: `{data.get('pre_message', '') or 'নেই'}`\n"
        f"🏷️ সক্রিয় মার্কার: `{marker_val}`\n"
        f"🔖 প্রশ্ন-ট্যাগ: `{data.get('q_tag', '') or 'নেই'}`\n"
        f"📝 ব্যাখ্যা-ট্যাগ: `{data.get('expl_tag', '') or 'নেই'}`\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ─── SEND QUIZ ────────────────────────────────────────────────────────────────
def build_question_text(q: dict, data: dict) -> str:
    """Build final question text with marker and q_tag."""
    text = q["question"]
    # Active marker
    marker_name = data.get("active_marker")
    if marker_name and marker_name in data.get("markers", {}):
        text = text + f" [{data['markers'][marker_name]}]"
    # q_tag
    q_tag = data.get("q_tag", "")
    if q_tag:
        text = text + f"\n{q_tag}"
    return text

def build_explanation(q: dict, data: dict) -> str:
    """Build explanation with expl_tag."""
    expl = q.get("explanation", "")
    expl_tag = data.get("expl_tag", "")
    if expl_tag and expl:
        expl = expl + f"\n{expl_tag}"
    elif expl_tag and not expl:
        expl = expl_tag
    return expl

async def cmd_send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not await auth_check(update, data):
        return

    if not data["questions"]:
        await update.message.reply_text("❌ কোনো প্রশ্ন নেই। আগে /upload_csv করুন।")
        return

    if not data["channels"]:
        await update.message.reply_text("❌ কোনো চ্যানেল নেই। আগে /add_channel করুন।")
        return

    # Build channel selection buttons
    buttons = [
        [InlineKeyboardButton(alias, callback_data=f"sendquiz:{cid}")]
        for alias, cid in data["channels"].items()
    ]
    await update.message.reply_text(
        f"📤 কোন চ্যানেলে পাঠাবেন? ({len(data['questions'])} টি প্রশ্ন)",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def cb_send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = load_data()
    channel_id = query.data.split(":", 1)[1]

    questions = data["questions"]
    total = len(questions)

    await query.edit_message_text(f"⏳ {total} টি পোল পাঠানো শুরু হচ্ছে...")

    # Send pre-message
    pre_msg = data.get("pre_message", "")
    if pre_msg:
        try:
            await context.bot.send_message(chat_id=channel_id, text=pre_msg)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Pre-message error: {e}")

    sent = 0
    for i, q in enumerate(questions):
        question_text = build_question_text(q, data)
        options = q["options"]
        correct_idx = get_correct_index(q)
        explanation = build_explanation(q, data)

        # Telegram poll question max 300 chars
        if len(question_text) > 300:
            question_text = question_text[:297] + "..."

        # Telegram poll option max 100 chars each
        options_trimmed = [opt[:100] for opt in options]

        try:
            await context.bot.send_poll(
                chat_id=channel_id,
                question=question_text,
                options=options_trimmed,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=explanation[:200] if explanation else None,
                is_anonymous=False,  # Changed to False to see who answered
            )
            sent += 1
            logger.info(f"Poll #{i+1}/{total} sent successfully")
        except Exception as e:
            logger.error(f"Poll #{i+1} error: {e}")

        await asyncio.sleep(1)  # 1 second between polls

    # Final score prompt
    try:
        score_msg = (
            f"✅ কুইজ শেষ! মোট {sent} টি প্রশ্ন পাঠানো হয়েছে।\n\n"
            f"👉 তোমার মোট স্কোর কত জানিয়ে দাও\n"
            f"`Your Score = ? / {sent}`"
        )
        await context.bot.send_message(chat_id=channel_id, text=score_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Score message error: {e}")

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"✅ সম্পন্ন! {sent}/{total} পোল পাঠানো হয়েছে।"
    )

# ─── CANCEL ──────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ বাতিল করা হয়েছে।")
    return ConversationHandler.END

# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    """Main function to run the bot"""
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation: Upload CSV
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("upload_csv", cmd_upload_csv)],
        states={AWAIT_CSV: [MessageHandler(filters.Document.ALL, receive_csv)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Add Channel
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_channel", cmd_add_channel)],
        states={AWAIT_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_id)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Pre-message
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("set_pre_message", cmd_set_pre_message)],
        states={AWAIT_PRE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pre_message)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Add Marker
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_marker", cmd_add_marker)],
        states={
            AWAIT_MARKER_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_marker_name)],
            AWAIT_MARKER_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_marker_value)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Use Marker (inline button)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("use_marker", cmd_use_marker)],
        states={AWAIT_SELECT_MARKER: [CallbackQueryHandler(cb_select_marker, pattern=r"^marker:")]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Q Tag
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("set_q_tag", cmd_set_q_tag)],
        states={AWAIT_Q_TAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_q_tag)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Conversation: Expl Tag
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("set_expl_tag", cmd_set_expl_tag)],
        states={AWAIT_EXPL_TAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_expl_tag)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    ))

    # Simple commands
    app.add_handler(CommandHandler("start",          cmd_start))
    app.add_handler(CommandHandler("list_channels",  cmd_list_channels))
    app.add_handler(CommandHandler("list_markers",   cmd_list_markers))
    app.add_handler(CommandHandler("no_marker",      cmd_no_marker))
    app.add_handler(CommandHandler("grant_access",   cmd_grant_access))
    app.add_handler(CommandHandler("revoke_access",  cmd_revoke_access))
    app.add_handler(CommandHandler("list_access",    cmd_list_access))
    app.add_handler(CommandHandler("status",         cmd_status))
    app.add_handler(CommandHandler("send_quiz",      cmd_send_quiz))
    app.add_handler(CommandHandler("cancel",         cmd_cancel))

    # Callback: send quiz to channel
    app.add_handler(CallbackQueryHandler(cb_send_quiz, pattern=r"^sendquiz:"))

    logger.info("🤖 Bot চালু হচ্ছে...")
    
    # Start the bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep the bot running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Bot বন্ধ করা হচ্ছে...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

# ─── RENDER COMPATIBLE ENTRY POINT ───────────────────────────────────────────
if __name__ == "__main__":
    # Render-এর জন্য event loop ফিক্স
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "event loop" in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(main())
        else:
            raise
