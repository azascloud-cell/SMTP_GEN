import os
import asyncio
import logging
from datetime import datetime, timezone
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from smtp_generator import SMTPGenerator

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")   # notif restart
RUN_ID      = os.environ.get("GITHUB_RUN_ID", "local")
REPO        = os.environ.get("GITHUB_REPOSITORY", "SMTP_GEN")

generator = SMTPGenerator()


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Generate SMTP Baru", callback_data="gen")],
        [InlineKeyboardButton("📋 Pilih Provider",     callback_data="menu_provider")],
        [InlineKeyboardButton("❓ Cara Pakai",          callback_data="howto")],
        [InlineKeyboardButton("📊 Status Bot",          callback_data="status")],
    ])


def provider_keyboard():
    providers = generator.list_providers()
    rows = []
    row  = []
    for i, p in enumerate(providers):
        row.append(InlineKeyboardButton(p, callback_data=f"prov_{p}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def format_smtp_result(result: dict) -> str:
    icon = "✅" if result["success"] else "❌"
    if not result["success"]:
        return f"{icon} *Gagal generate:* {result.get('error', 'Unknown error')}"

    data = result["data"]
    return (
        f"📧 *SMTP Credentials Berhasil Digenerate!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 *Email:* `{data['email']}`\n"
        f"🔑 *Password:* `{data['password']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 *Provider:* {data['provider']}\n"
        f"📤 *SMTP Host:* `{data['smtp_host']}`\n"
        f"🔌 *SMTP Port:* `{data['smtp_port']}`\n"
        f"🔒 *SSL/TLS:* `{data['ssl']}`\n"
        f"👤 *Username:* `{data['email']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *IMAP Host:* `{data['imap_host']}`\n"
        f"🔌 *IMAP Port:* `{data['imap_port']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ *Expires:* {data.get('expires', 'Unknown')}\n"
        f"📝 *Note:* {data.get('note', '-')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Simpan credentials ini sebelum kadaluarsa!_"
    )


# ── Handlers ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *SMTP Generator Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Bot ini generate *SMTP credentials* otomatis dari berbagai provider\n"
        "email sementara yang siap pakai untuk kirim & terima email.\n\n"
        "Pilih menu di bawah untuk mulai:"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Sedang generate SMTP credentials...")
    result = await asyncio.to_thread(generator.generate_random)
    await msg.edit_text(
        format_smtp_result(result),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Generate Lagi", callback_data="gen")],
            [InlineKeyboardButton("🏠 Menu Utama",   callback_data="back_main")],
        ]),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    providers_ok = generator.list_providers()
    text = (
        f"📊 *Status Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Status:* Online\n"
        f"⏰ *Waktu Sekarang:* {now_utc()}\n"
        f"🆔 *Run ID:* `{RUN_ID}`\n"
        f"📦 *Repo:* `{REPO}`\n"
        f"🔧 *Provider Aktif:* {len(providers_ok)}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ *Cara Pakai SMTP Generator Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ Ketik /generate atau tekan tombol *Generate SMTP Baru*\n"
        "2️⃣ Bot akan generate email + password otomatis\n"
        "3️⃣ Gunakan credentials tersebut di aplikasi kamu\n\n"
        "📌 *Commands:*\n"
        "/start   — Menu utama\n"
        "/generate — Generate SMTP sekarang\n"
        "/status  — Cek status bot\n"
        "/help    — Bantuan ini\n\n"
        "⚠️ *Catatan:*\n"
        "• Email yang digenerate bersifat *sementara* (temporary)\n"
        "• Cocok untuk testing, registrasi, atau keperluan privasi\n"
        "• Jangan pakai untuk data penting atau sensitif\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Callback Query Handler ─────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "gen":
        await query.edit_message_text("⏳ Sedang generate SMTP credentials...")
        result = await asyncio.to_thread(generator.generate_random)
        await query.edit_message_text(
            format_smtp_result(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi", callback_data="gen")],
                [InlineKeyboardButton("🏠 Menu Utama",   callback_data="back_main")],
            ]),
        )

    elif data == "menu_provider":
        await query.edit_message_text(
            "🔧 *Pilih Provider Email:*\nPilih provider yang ingin kamu gunakan:",
            parse_mode="Markdown",
            reply_markup=provider_keyboard(),
        )

    elif data.startswith("prov_"):
        provider = data[5:]
        await query.edit_message_text(f"⏳ Generate dari provider *{provider}*...", parse_mode="Markdown")
        result = await asyncio.to_thread(generator.generate_by_provider, provider)
        await query.edit_message_text(
            format_smtp_result(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",      callback_data=f"prov_{provider}")],
                [InlineKeyboardButton("📋 Ganti Provider",     callback_data="menu_provider")],
                [InlineKeyboardButton("🏠 Menu Utama",         callback_data="back_main")],
            ]),
        )

    elif data == "howto":
        text = (
            "📖 *Cara Menggunakan SMTP Credentials*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Setelah generate, gunakan data ini di aplikasi:\n\n"
            "📤 *Untuk kirim email (SMTP):*\n"
            "• Host: sesuai hasil generate\n"
            "• Port: 587 (TLS) atau 465 (SSL)\n"
            "• Username: alamat email yang digenerate\n"
            "• Password: password yang digenerate\n\n"
            "📥 *Untuk terima email (IMAP):*\n"
            "• Host: sesuai hasil generate\n"
            "• Port: 993\n"
            "• SSL: aktif\n\n"
            "🐍 *Contoh Python:*\n"
            "```\nimport smtplib\nserver = smtplib.SMTP('host', 587)\nserver.starttls()\nserver.login('email', 'pass')\n```"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]])
        )

    elif data == "status":
        providers_ok = generator.list_providers()
        text = (
            f"📊 *Status Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 *Status:* Online\n"
            f"⏰ *Waktu:* {now_utc()}\n"
            f"🆔 *Run ID:* `{RUN_ID}`\n"
            f"🔧 *Provider Aktif:* {len(providers_ok)}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]])
        )

    elif data == "back_main":
        await query.edit_message_text(
            "🤖 *SMTP Generator Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih menu di bawah untuk mulai:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ── Startup Notification ───────────────────────────────────────────────────────
async def send_startup_notification(bot: Bot):
    if not ADMIN_CHAT:
        logger.warning("TELEGRAM_CHAT_ID tidak diset, skip notifikasi startup.")
        return
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT,
            text=(
                "🚀 *Bot SMTP Generator Aktif!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ *Waktu:* {now_utc()}\n"
                f"🆔 *Run ID:* `{RUN_ID}`\n"
                f"📦 *Repo:* `{REPO}`\n"
                f"🔧 *Provider:* {len(generator.list_providers())} provider aktif\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ Bot siap menerima perintah!"
            ),
            parse_mode="Markdown",
        )
        logger.info("Startup notification terkirim.")
    except Exception as e:
        logger.error(f"Gagal kirim notifikasi startup: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def post_init(application: Application):
        await send_startup_notification(application.bot)

    app.post_init = post_init

    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
