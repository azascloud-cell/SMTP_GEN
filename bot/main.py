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
    MessageHandler,
    filters,
    ConversationHandler,
)
from smtp_generator import SMTPGenerator
from smtp_manager import SMTPManager

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
RUN_ID     = os.environ.get("GITHUB_RUN_ID", "local")
REPO       = os.environ.get("GITHUB_REPOSITORY", "SMTP_GEN")

generator = SMTPGenerator()
manager   = SMTPManager()

# ConversationHandler state
WAITING_SMTP_CREDS = 1


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 Generate Email Temp",    callback_data="gen")],
        [InlineKeyboardButton("📋 Pilih Provider Email",   callback_data="menu_provider")],
        [InlineKeyboardButton("➕ Tambah SMTP Real",        callback_data="add_smtp_info")],
        [InlineKeyboardButton("📂 Daftar SMTP Tersimpan",  callback_data="list_smtp")],
        [InlineKeyboardButton("❓ Cara Pakai",              callback_data="howto")],
        [InlineKeyboardButton("📊 Status Bot",             callback_data="status")],
    ])


def provider_keyboard():
    providers = generator.list_providers()
    rows, row = [], []
    for i, p in enumerate(providers):
        row.append(InlineKeyboardButton(p, callback_data=f"prov_{p}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def format_tempmail_result(result: dict) -> str:
    """Format hasil generate email sementara – jelas bahwa ini receive-only."""
    if not result["success"]:
        return f"❌ *Gagal generate:* {result.get('error', 'Unknown error')}"

    d = result["data"]
    return (
        f"📧 *Email Sementara Berhasil Digenerate!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 *Email:* `{d['email']}`\n"
        f"🔑 *Web Password:* `{d['password']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 *Provider:* {d['provider']}\n"
        f"⏱ *Kadaluarsa:* {d.get('expires', '-')}\n"
        f"🔗 *Cek Inbox:* {d.get('note', '-')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *CATATAN PENTING:*\n"
        f"• Email ini hanya untuk *MENERIMA* pesan\n"
        f"• Tidak bisa dipakai untuk login SMTP/IMAP\n"
        f"• Untuk kirim email, gunakan menu *Tambah SMTP Real*\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


def format_smtp_add_success(result: dict) -> str:
    smtp_ok = "✅" if result.get("smtp_ok") else "❌"
    imap_ok = "✅" if result.get("imap_ok") else "⚠️"
    return (
        f"✅ *SMTP Berhasil Ditambahkan!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 *Email:* `{result['email']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 *SMTP Host:* `{result['smtp_host']}`\n"
        f"🔌 *SMTP Port:* `{result['smtp_port']}`\n"
        f"{smtp_ok} *SMTP Terverifikasi:* Ya\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *IMAP Host:* `{result['imap_host']}`\n"
        f"🔌 *IMAP Port:* 993\n"
        f"{imap_ok} *IMAP:* {'Terverifikasi' if result.get('imap_ok') else 'Tidak tersedia'}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Akun tersimpan dan siap digunakan!"
    )


def format_smtp_add_fail(result: dict) -> str:
    return (
        f"❌ *SMTP Gagal Ditambahkan!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 *Langkah gagal:* {result.get('step', 'Validasi')}\n"
        f"⚠️ *Error:* {result.get('error', 'Unknown')}\n"
        f"🌐 *Host dicoba:* `{result.get('tried_host', '-')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Tips:*\n"
        f"{result.get('hint', 'Pastikan email & password benar')}\n\n"
        f"Untuk Gmail → pakai *App Password* bukan password biasa:\n"
        f"👉 myaccount\\.google\\.com/apppasswords"
    )


# ── Command Handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *SMTP Generator Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Bot untuk generate email sementara & kelola akun SMTP nyata\\.\n\n"
        "📧 *Email Temp* — untuk menerima pesan \\(gratis, sekali pakai\\)\n"
        "📤 *SMTP Real* — tambah Gmail/Yahoo dengan App Password \\(untuk kirim\\)\n\n"
        "Pilih menu di bawah:",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Generate email sementara...")
    result = await asyncio.to_thread(generator.generate_random)
    await msg.edit_text(
        format_tempmail_result(result),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Generate Lagi",       callback_data="gen")],
            [InlineKeyboardButton("➕ Tambah SMTP Real",     callback_data="add_smtp_info")],
            [InlineKeyboardButton("🏠 Menu Utama",          callback_data="back_main")],
        ]),
    )


async def cmd_addsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addsmtp email@gmail.com|app_password
    atau  /addsmtp email@gmail.com | app_password
    """
    args_raw = " ".join(context.args).strip() if context.args else ""

    # Pisah dengan | atau spasi+|+spasi
    if "|" in args_raw:
        parts = args_raw.split("|", 1)
    else:
        await update.message.reply_text(
            "➕ *Tambah Akun SMTP Real*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format perintah:\n"
            "`/addsmtp email@gmail.com|app_password`\n\n"
            "Contoh Gmail:\n"
            "`/addsmtp john@gmail.com|abcd efgh ijkl mnop`\n\n"
            "💡 *Cara dapat App Password Gmail:*\n"
            "1\\. Buka myaccount\\.google\\.com/apppasswords\n"
            "2\\. Pilih *Mail* → *Other*\n"
            "3\\. Salin 16 karakter yang muncul",
            parse_mode="MarkdownV2",
        )
        return

    email    = parts[0].strip()
    password = parts[1].strip()

    if not email or not password:
        await update.message.reply_text("⚠️ Format salah. Gunakan: `/addsmtp email|password`", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(
        f"🔄 *Memverifikasi koneksi SMTP...*\n"
        f"📧 `{email}`\n"
        f"⏳ Mohon tunggu\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    result = await asyncio.to_thread(manager.add_account, email, password)

    if result["success"]:
        await msg.edit_text(
            format_smtp_add_success(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Lihat Semua Akun", callback_data="list_smtp")],
                [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
            ]),
        )
    else:
        await msg.edit_text(
            format_smtp_add_fail(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Coba Lagi",  callback_data="add_smtp_info")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
            ]),
        )


async def cmd_listsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = manager.list_accounts()
    if not accounts:
        await update.message.reply_text(
            "📭 *Belum ada akun SMTP tersimpan.*\n\n"
            "Tambah akun dengan perintah:\n"
            "`/addsmtp email@gmail.com|app_password`",
            parse_mode="Markdown",
        )
        return

    lines = ["📂 *Daftar Akun SMTP Tersimpan*\n━━━━━━━━━━━━━━━━━━━━"]
    for i, acc in enumerate(accounts, 1):
        ok = "✅" if acc["verified"] else "⚠️"
        lines.append(f"{i}\\. {ok} `{acc['email']}`\n   🌐 {acc['smtp_host']}:{acc['smtp_port']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total: {len(accounts)} akun")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Tambah Akun Baru", callback_data="add_smtp_info")],
            [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
        ]),
    )


async def cmd_delsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = " ".join(context.args).strip() if context.args else ""
    if not email:
        await update.message.reply_text(
            "Format: `/delsmtp email@gmail.com`", parse_mode="Markdown"
        )
        return
    result = manager.remove_account(email)
    if result["success"]:
        await update.message.reply_text(f"✅ Akun `{email}` berhasil dihapus.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ {result['error']}", parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 *Status Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Status:* Online\n"
        f"⏰ *Waktu:* {now_utc()}\n"
        f"🆔 *Run ID:* `{RUN_ID}`\n"
        f"📦 *Repo:* `{REPO}`\n"
        f"🔧 *Provider Temp:* {len(generator.list_providers())}\n"
        f"📂 *Akun SMTP Real:* {manager.count()}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Panduan SMTP Generator Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📧 *1\\. Email Sementara \\(Receive Only\\)*\n"
        "Gunakan /generate atau tombol menu\\.\n"
        "Email ini HANYA untuk *menerima* pesan\\.\n"
        "Tidak bisa login SMTP/IMAP dengan password random\\.\n\n"
        "📤 *2\\. SMTP Real \\(Kirim & Terima\\)*\n"
        "`/addsmtp email@gmail.com|app_password`\n"
        "Bot akan verifikasi koneksi sebelum menyimpan\\.\n\n"
        "📋 *Commands:*\n"
        "/start     — Menu utama\n"
        "/generate  — Generate email temp\n"
        "/addsmtp   — Tambah akun SMTP real\n"
        "/listsmtp  — Lihat akun tersimpan\n"
        "/delsmtp   — Hapus akun\n"
        "/status    — Status bot\n\n"
        "💡 *Gmail App Password:*\n"
        "myaccount\\.google\\.com/apppasswords\n"
        "━━━━━━━━━━━━━━━━━━━━",
        parse_mode="MarkdownV2",
    )


# ── Callback Query Handler ─────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "gen":
        await query.edit_message_text("⏳ Generate email sementara...")
        result = await asyncio.to_thread(generator.generate_random)
        await query.edit_message_text(
            format_tempmail_result(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",   callback_data="gen")],
                [InlineKeyboardButton("➕ Tambah SMTP Real", callback_data="add_smtp_info")],
                [InlineKeyboardButton("🏠 Menu Utama",      callback_data="back_main")],
            ]),
        )

    elif data == "menu_provider":
        await query.edit_message_text(
            "🔧 *Pilih Provider Email Sementara:*",
            parse_mode="Markdown",
            reply_markup=provider_keyboard(),
        )

    elif data.startswith("prov_"):
        provider = data[5:]
        await query.edit_message_text(f"⏳ Generate dari *{provider}*...", parse_mode="Markdown")
        result = await asyncio.to_thread(generator.generate_by_provider, provider)
        await query.edit_message_text(
            format_tempmail_result(result),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",       callback_data=f"prov_{provider}")],
                [InlineKeyboardButton("📋 Ganti Provider",      callback_data="menu_provider")],
                [InlineKeyboardButton("🏠 Menu Utama",          callback_data="back_main")],
            ]),
        )

    elif data == "add_smtp_info":
        await query.edit_message_text(
            "➕ *Tambah Akun SMTP Real*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim perintah berikut di chat:\n\n"
            "`/addsmtp email@gmail.com|app_password`\n\n"
            "📌 *Contoh Gmail:*\n"
            "`/addsmtp john@gmail.com|abcd efgh ijkl mnop`\n\n"
            "💡 *Cara dapat App Password Gmail:*\n"
            "1. Buka myaccount.google.com/apppasswords\n"
            "2. Pilih *Mail* → *Other (Custom name)*\n"
            "3. Generate → salin 16 karakter\n\n"
            "✅ Bot akan verifikasi koneksi sebelum menyimpan.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "list_smtp":
        accounts = manager.list_accounts()
        if not accounts:
            text = (
                "📭 *Belum ada akun SMTP tersimpan.*\n\n"
                "Tambah dengan: `/addsmtp email|password`"
            )
        else:
            lines = ["📂 *Akun SMTP Tersimpan*\n━━━━━━━━━━━━━━━━━━━━"]
            for i, acc in enumerate(accounts, 1):
                ok = "✅" if acc["verified"] else "⚠️"
                lines.append(f"{i}. {ok} `{acc['email']}`\n   🌐 {acc['smtp_host']}:{acc['smtp_port']}")
            lines.append(f"\n━━━━━━━━━━━━━━━━━━━━\nTotal: {len(accounts)} akun")
            text = "\n".join(lines)

        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Tambah Baru", callback_data="add_smtp_info")],
                [InlineKeyboardButton("🔙 Kembali",    callback_data="back_main")],
            ]),
        )

    elif data == "howto":
        await query.edit_message_text(
            "📖 *Perbedaan Email Temp vs SMTP Real*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📧 *Email Sementara (Generate)*\n"
            "• Gratis, langsung jadi\n"
            "• Hanya bisa MENERIMA email\n"
            "• Cek inbox via website provider\n"
            "• Tidak bisa login SMTP/IMAP\n\n"
            "📤 *SMTP Real (Tambah Akun)*\n"
            "• Pakai Gmail/Yahoo dengan App Password\n"
            "• Bisa KIRIM & TERIMA email\n"
            "• Terverifikasi sebelum disimpan\n"
            "• Aman – menggunakan koneksi TLS\n\n"
            "💡 Gmail App Password:\n"
            "myaccount.google.com/apppasswords",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "status":
        await query.edit_message_text(
            f"📊 *Status Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Status: Online\n"
            f"⏰ Waktu: {now_utc()}\n"
            f"🆔 Run ID: `{RUN_ID}`\n"
            f"🔧 Provider Temp: {len(generator.list_providers())}\n"
            f"📂 Akun SMTP Real: {manager.count()}\n"
            f"━━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "back_main":
        await query.edit_message_text(
            "🤖 *SMTP Generator Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih menu di bawah:",
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
                f"🔧 *Provider Temp:* {len(generator.list_providers())}\n"
                f"📂 *Akun SMTP Real:* {manager.count()}\n"
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
    app.add_handler(CommandHandler("addsmtp",  cmd_addsmtp))
    app.add_handler(CommandHandler("listsmtp", cmd_listsmtp))
    app.add_handler(CommandHandler("delsmtp",  cmd_delsmtp))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def post_init(application: Application):
        await send_startup_notification(application.bot)
        # Set bot commands
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("start",    "Menu utama"),
            BotCommand("generate", "Generate email sementara"),
            BotCommand("addsmtp",  "Tambah akun SMTP real"),
            BotCommand("listsmtp", "Daftar akun SMTP tersimpan"),
            BotCommand("delsmtp",  "Hapus akun SMTP"),
            BotCommand("status",   "Status bot"),
            BotCommand("help",     "Bantuan"),
        ])

    app.post_init = post_init

    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
