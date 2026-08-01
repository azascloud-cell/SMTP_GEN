import os
import asyncio
import logging
import time
from datetime import datetime, timezone
from github_updater import (
    get_latest_commit,
    get_cached_commit,
    save_commit,
    trigger_update,
    check_for_update,
)
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from smtp_generator import SMTPGenerator
from smtp_manager import SMTPManager
from whatsapp_fix import (
    send_appeal_email,
    check_whatsapp_reply,
    add_pending,
    remove_pending,
    get_all_pending,
    mark_notified,
)
from cpanel_generator import (
    create_email as cpanel_create,
    delete_email as cpanel_delete,
    list_emails as cpanel_list,
    test_connection as cpanel_test,
    is_configured as cpanel_configured,
    CPANEL_DOMAIN,
)

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


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main_menu_keyboard():
    cpanel_ok = cpanel_configured()
    rows = []
    if cpanel_ok:
        rows.append([InlineKeyboardButton("⚡ Generate SMTP Real (cPanel)", callback_data="cpanel_gen")])
        rows.append([InlineKeyboardButton("📂 Lihat Email cPanel",          callback_data="cpanel_list")])
    rows.append([InlineKeyboardButton("📧 Email Temp (Receive Only)",      callback_data="gen")])
    rows.append([InlineKeyboardButton("📋 Pilih Provider Email Temp",      callback_data="menu_provider")])
    rows.append([InlineKeyboardButton("➕ Tambah SMTP Manual",              callback_data="add_smtp_info")])
    rows.append([InlineKeyboardButton("📂 Akun SMTP Manual",               callback_data="list_smtp")])
    if not cpanel_ok:
        rows.append([InlineKeyboardButton("🔧 Setup cPanel (Generate Real)", callback_data="cpanel_setup")])
    rows.append([InlineKeyboardButton("🔧 WhatsApp Fix (/fix +nomor)", callback_data="fix_info")])
    rows.append([InlineKeyboardButton("🔄 Cek & Update Bot",          callback_data="check_update")])
    rows.append([InlineKeyboardButton("❓ Cara Pakai",  callback_data="howto"),
                 InlineKeyboardButton("📊 Status Bot", callback_data="status")])
    return InlineKeyboardMarkup(rows)


def provider_keyboard():
    providers = generator.list_providers()
    rows, row = [], []
    for p in providers:
        row.append(InlineKeyboardButton(p, callback_data=f"prov_{p}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


# ── Formatters ────────────────────────────────────────────────────────────────
def fmt_cpanel_success(d: dict) -> str:
    return (
        f"✅ *Email SMTP Real Berhasil Dibuat!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 *Email:* `{d['email']}`\n"
        f"🔑 *Password:* `{d['password']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 *SMTP Host:* `{d['smtp_host']}`\n"
        f"🔌 *SMTP Port:* `{d['smtp_port']}` (STARTTLS) / `{d['smtp_port_ssl']}` (SSL)\n"
        f"👤 *Username:* `{d['email']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *IMAP Host:* `{d['imap_host']}`\n"
        f"🔌 *IMAP Port:* `{d['imap_port']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 *Webmail:* {d.get('webmail', '-')}\n"
        f"⏱ *Expires:* {d.get('expires', '-')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Akun ini *bisa kirim & terima* email nyata!"
    )


def fmt_cpanel_fail(result: dict) -> str:
    setup_needed = result.get("setup_needed", False)
    if setup_needed:
        return (
            f"⚠️ *cPanel Belum Dikonfigurasi*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Untuk generate SMTP real, kamu perlu setup hosting gratis dulu.\n\n"
            f"Tekan tombol *Setup cPanel* di menu utama untuk panduan lengkap."
        )
    return (
        f"❌ *Gagal Buat Email cPanel*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Error:* {result.get('error', 'Unknown')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Coba cek konfigurasi cPanel di GitHub Secrets."
    )


def fmt_tempmail(result: dict) -> str:
    if not result["success"]:
        return f"❌ *Gagal generate:* {result.get('error', 'Unknown')}"
    d = result["data"]
    return (
        f"📧 *Email Sementara*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 *Email:* `{d['email']}`\n"
        f"⏱ *Expires:* {d.get('expires', '-')}\n"
        f"🔗 *Cek inbox:* {d.get('note', '-')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Ini hanya untuk MENERIMA email*\n"
        f"Untuk kirim email, gunakan menu ⚡ *Generate SMTP Real*"
    )


def fmt_smtp_add_ok(r: dict) -> str:
    smtp_ok = "✅" if r.get("smtp_ok") else "❌"
    imap_ok = "✅" if r.get("imap_ok") else "⚠️"
    return (
        f"✅ *SMTP Berhasil Ditambahkan!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 *Email:* `{r['email']}`\n"
        f"📤 *SMTP:* `{r['smtp_host']}:{r['smtp_port']}`  {smtp_ok}\n"
        f"📥 *IMAP:* `{r['imap_host']}:993`  {imap_ok}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Tersimpan & siap digunakan!"
    )


def fmt_smtp_add_fail(r: dict) -> str:
    return (
        f"❌ *SMTP Gagal Ditambahkan*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 Gagal di: {r.get('step', 'Validasi')}\n"
        f"⚠️ Error: {r.get('error', 'Unknown')}\n"
        f"🌐 Host: `{r.get('tried_host', '-')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 {r.get('hint', 'Pastikan email & password benar')}\n\n"
        f"Gmail → pakai App Password:\n"
        f"myaccount.google.com/apppasswords"
    )


# ── Command Handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpanel_ok  = cpanel_configured()
    mode_label = f"✅ cPanel aktif: `{CPANEL_DOMAIN}`" if cpanel_ok else "⚠️ cPanel belum setup"
    await update.message.reply_text(
        f"🤖 *SMTP Generator Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 Mode: {mode_label}\n\n"
        f"⚡ *SMTP Real* — email yang bisa kirim & terima \\(via cPanel\\)\n"
        f"📧 *Email Temp* — hanya untuk menerima \\(web only\\)\n"
        f"➕ *SMTP Manual* — tambah Gmail/Yahoo dengan App Password\n\n"
        f"Pilih menu di bawah:",
        parse_mode="MarkdownV2",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate email temp (receive-only)."""
    msg = await update.message.reply_text("⏳ Generate email sementara...")
    result = await asyncio.to_thread(generator.generate_random)
    await msg.edit_text(
        fmt_tempmail(result), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Generate Lagi",         callback_data="gen")],
            [InlineKeyboardButton("⚡ Coba Generate SMTP Real", callback_data="cpanel_gen")],
            [InlineKeyboardButton("🏠 Menu Utama",            callback_data="back_main")],
        ]),
    )


async def cmd_cpanel_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate email SMTP real via cPanel."""
    msg = await update.message.reply_text("⏳ Membuat akun email real via cPanel...")
    result = await asyncio.to_thread(cpanel_create)
    if result["success"]:
        text = fmt_cpanel_success(result)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Generate Lagi",    callback_data="cpanel_gen")],
            [InlineKeyboardButton("📂 Lihat Semua Akun", callback_data="cpanel_list")],
            [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
        ])
    else:
        text = fmt_cpanel_fail(result)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 Panduan Setup", callback_data="cpanel_setup")],
            [InlineKeyboardButton("🏠 Menu Utama",    callback_data="back_main")],
        ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_addsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args_raw = " ".join(context.args).strip() if context.args else ""
    if "|" not in args_raw:
        await update.message.reply_text(
            "➕ *Tambah SMTP Manual*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addsmtp email|password`\n\n"
            "Contoh Gmail:\n"
            "`/addsmtp john@gmail.com|abcd efgh ijkl mnop`\n\n"
            "💡 Gmail App Password:\n"
            "myaccount.google.com/apppasswords",
            parse_mode="Markdown",
        )
        return
    parts    = args_raw.split("|", 1)
    email    = parts[0].strip()
    password = parts[1].strip()
    if not email or not password:
        await update.message.reply_text("⚠️ Format: `/addsmtp email|password`", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(f"🔄 Verifikasi SMTP `{email}`...", parse_mode="Markdown")
    result = await asyncio.to_thread(manager.add_account, email, password)
    text   = fmt_smtp_add_ok(result) if result["success"] else fmt_smtp_add_fail(result)
    kb     = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Lihat Akun", callback_data="list_smtp")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
    ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_listsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = manager.list_accounts()
    if not accounts:
        text = "📭 *Belum ada akun SMTP manual.*\n\nTambah: `/addsmtp email|password`"
    else:
        lines = ["📂 *Akun SMTP Manual*\n━━━━━━━━━━━━━━━━━━━━"]
        for i, a in enumerate(accounts, 1):
            ok = "✅" if a["verified"] else "⚠️"
            lines.append(f"{i}. {ok} `{a['email']}`")
        lines.append(f"\nTotal: {len(accounts)} akun")
        text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Tambah", callback_data="add_smtp_info")],
            [InlineKeyboardButton("🔙 Menu",   callback_data="back_main")],
        ]))


async def cmd_delsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = " ".join(context.args).strip() if context.args else ""
    if not email:
        await update.message.reply_text("Format: `/delsmtp email@domain.com`", parse_mode="Markdown")
        return
    result = manager.remove_account(email)
    icon   = "✅" if result["success"] else "❌"
    msg    = f"{icon} `{email}` {'dihapus.' if result['success'] else result['error']}"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cpanel_ok = cpanel_configured()
    cp_status = f"✅ Aktif (`{CPANEL_DOMAIN}`)" if cpanel_ok else "⚠️ Belum setup"
    await update.message.reply_text(
        f"📊 *Status Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Bot: Online\n"
        f"⏰ Waktu: {now_utc()}\n"
        f"🆔 Run ID: `{RUN_ID}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔧 cPanel: {cp_status}\n"
        f"📧 Provider Temp: {len(generator.list_providers())}\n"
        f"📂 Akun Manual: {manager.count()}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Panduan Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ *Generate SMTP Real* \\(cPanel\\)\n"
        "Buat email nyata yg bisa kirim & terima\n"
        "Butuh setup cPanel dulu \\(lihat /cpanelsetup\\)\n\n"
        "📧 *Email Temp* \\(Receive Only\\)\n"
        "Langsung generate, tapi hanya untuk menerima\n\n"
        "➕ *SMTP Manual*\n"
        "`/addsmtp email|app_password`\n"
        "Tambah Gmail/Yahoo, diverifikasi otomatis\n\n"
        "🔧 *WhatsApp Fix*\n"
        "`/fix +628xxxxxxxx`\n"
        "Kirim email banding ke WhatsApp support\n"
        "Bot monitor balasan & notif otomatis\\!\n\n"
        "📋 *Commands:*\n"
        "/start — Menu utama\n"
        "/generate — Email temp\n"
        "/cpanelgen — Generate SMTP real\n"
        "/addsmtp — Tambah SMTP manual\n"
        "/listsmtp — Lihat akun manual\n"
        "/delsmtp — Hapus akun manual\n"
        "/fix — Banding ban WhatsApp\n"
        "/cpanelsetup — Panduan setup hosting\n"
        "/status — Status bot",
        parse_mode="MarkdownV2",
    )


# ── WhatsApp Fix Command ───────────────────────────────────────────────────────
async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kirim email banding WhatsApp ban ke support@support.whatsapp.com."""
    phone = " ".join(context.args).strip() if context.args else ""

    if not phone:
        await update.message.reply_text(
            "🔧 *WhatsApp Fix — Banding Ban*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Format: `/fix +628xxxxxxxx`\n\n"
            "Contoh:\n"
            "`/fix +6281234567890`\n"
            "`/fix +249114757586`\n\n"
            "📌 Bot akan:\n"
            "1️⃣ Kirim email banding ke WhatsApp Support\n"
            "2️⃣ Monitor inbox IMAP untuk balasan\n"
            "3️⃣ Notifikasi kamu otomatis saat ada balasan\n\n"
            "💡 Pastikan ada akun SMTP manual dulu: `/addsmtp`",
            parse_mode="Markdown",
        )
        return

    # Normalise phone
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    # Ambil akun SMTP pertama yang verified
    accounts = manager.list_accounts()
    verified = [a for a in accounts if a.get("verified")]
    if not verified:
        await update.message.reply_text(
            "❌ *Tidak ada akun SMTP yang aktif\\!*\n\n"
            "Tambah dulu dengan:\n"
            "`/addsmtp email@gmail.com|app_password`\n\n"
            "📌 Gmail App Password:\n"
            "myaccount\\.google\\.com/apppasswords",
            parse_mode="MarkdownV2",
        )
        return

    smtp_email = verified[0]["email"]
    # Ambil data lengkap (dengan password)
    smtp_full = manager.get_account(smtp_email)
    if not smtp_full:
        await update.message.reply_text("❌ Gagal ambil data akun SMTP.", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(
        f"📤 *Mengirim email banding...*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Nomor: `{phone}`\n"
        f"📧 SMTP: `{smtp_email}`\n"
        f"🎯 Ke: `support@support.whatsapp.com`",
        parse_mode="Markdown",
    )

    sent_at = time.time()
    result  = await asyncio.to_thread(send_appeal_email, smtp_full, phone)

    if not result["success"]:
        await msg.edit_text(
            f"❌ *Gagal Kirim Email Banding*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Nomor: `{phone}`\n"
            f"⚠️ Error: {result.get('error', 'Unknown')}\n\n"
            f"💡 Cek App Password atau coba akun SMTP lain.",
            parse_mode="Markdown",
        )
        return

    # Simpan ke pending monitor
    chat_id = update.effective_chat.id
    key     = add_pending(chat_id, phone, smtp_email, sent_at)

    await msg.edit_text(
        f"📬 *EMAIL BANDING TERKIRIM!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Nomor : `{phone}`\n"
        f"📧 SMTP  : `{smtp_email}`\n"
        f"✅ Status : Banding berhasil terkirim!\n\n"
        f"🤖 *INFO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Bot akan monitor inbox secara otomatis.\n"
        f"Kamu akan dinotifikasi jika ada balasan dari WhatsApp.",
        parse_mode="Markdown",
    )


async def imap_monitor_loop(bot: Bot):
    """Background loop: cek balasan WhatsApp tiap 5 menit."""
    logger.info("IMAP monitor loop dimulai...")
    while True:
        try:
            pending = get_all_pending()
            for key, item in list(pending.items()):
                if item.get("notified"):
                    continue

                smtp_full = manager.get_account(item["smtp_email"])
                if not smtp_full:
                    continue

                reply = await asyncio.to_thread(
                    check_whatsapp_reply, smtp_full, item["sent_at"]
                )

                if reply:
                    chat_id = item["chat_id"]
                    phone   = item["phone"]
                    smtp_em = item["smtp_email"]

                    body_preview = reply.get("body", "")[:400]

                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"📬 *EMAIL DIBALAS — SUKSES!*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📱 Nomor : `{phone}`\n"
                                f"📧 SMTP  : `{smtp_em}`\n"
                                f"✅ Status : Banding berhasil terkirim!\n\n"
                                f"🤖 *PESAN DARI AI ASISTEN*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"👋 Hallo, kabar gembira nih!\n\n"
                                f"Email banding kamu sudah dibalas positif — "
                                f"kemungkinan besar nomor kamu sudah berhasil "
                                f"diaktifkan kembali! Coba buka WhatsApp dan "
                                f"cek nomor kamu ya. Kalau masih ada kendala, "
                                f"coba banding ulang dengan /fix.\n\n"
                                f"📩 *ISI BALASAN EMAIL*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📧 SMTP Dipakai: `{smtp_em}`\n"
                                f"📧 Dari: {reply.get('from', '-')}\n"
                                f"📌 Subject: {reply.get('subject', '-')}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{body_preview}"
                            ),
                            parse_mode="Markdown",
                        )
                        mark_notified(key)
                        logger.info(f"Notifikasi balasan terkirim ke {chat_id} untuk {phone}")
                    except Exception as e:
                        logger.error(f"Gagal kirim notif ke {chat_id}: {e}")

        except Exception as e:
            logger.error(f"IMAP monitor error: {e}")

        await asyncio.sleep(300)  # Cek tiap 5 menit


# ── GitHub Auto-Update ────────────────────────────────────────────────────────
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cek & trigger update dari GitHub repo terbaru."""
    msg = await update.message.reply_text("🔍 Memeriksa update dari GitHub...")

    result = await asyncio.to_thread(check_for_update)

    if "error" in result and not result.get("update_available"):
        await msg.edit_text(
            f"❌ *Gagal cek update*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ {result['error']}\n\n"
            f"💡 Pastikan secret `GH_PAT` sudah diset di GitHub Actions.",
            parse_mode="Markdown",
        )
        return

    current = result.get("current_sha", "?")
    latest  = result.get("latest_sha", "?")
    info    = result.get("commit_info", {})

    if not result.get("update_available"):
        await msg.edit_text(
            f"✅ *Bot sudah versi terbaru!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 Commit: `{latest}`\n"
            f"📝 {info.get('message', '-')}\n"
            f"👤 {info.get('author', '-')} · {info.get('date', '-')}\n"
            f"⏰ {now_utc()}",
            parse_mode="Markdown",
        )
        return

    # Ada update — trigger workflow
    await msg.edit_text(
        f"🔄 *Update ditemukan! Trigger restart...*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Saat ini : `{current}`\n"
        f"🆕 Terbaru  : `{latest}`\n"
        f"📝 {info.get('message', '-')}\n"
        f"👤 {info.get('author', '-')} · {info.get('date', '-')}",
        parse_mode="Markdown",
    )

    trig = await asyncio.to_thread(trigger_update)
    if trig["success"]:
        await msg.edit_text(
            f"🚀 *Update berhasil di-trigger!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Dari : `{current}` → `{latest}`\n"
            f"📝 {info.get('message', '-')}\n\n"
            f"⏳ Bot akan restart dalam ~30 detik dengan kode terbaru.",
            parse_mode="Markdown",
        )
    else:
        await msg.edit_text(
            f"❌ *Gagal trigger update*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ {trig.get('error', 'Unknown')}\n\n"
            f"💡 Cek secret `GH_PAT` punya scope `workflow`.",
            parse_mode="Markdown",
        )


async def auto_update_loop(bot: Bot):
    """Background loop: cek GitHub update tiap 30 menit, restart otomatis."""
    logger.info("Auto-update loop dimulai...")
    # Simpan commit saat ini
    current_sha = os.environ.get("GITHUB_SHA", "")
    if current_sha:
        await asyncio.to_thread(save_commit, current_sha)

    # Tunda 5 menit sebelum cek pertama (biarkan bot fully up dulu)
    await asyncio.sleep(300)

    while True:
        try:
            result = await asyncio.to_thread(check_for_update)

            if result.get("update_available"):
                latest = result.get("latest_sha", "?")
                current = result.get("current_sha", "?")
                info   = result.get("commit_info", {})
                logger.info(f"Update tersedia: {current} → {latest}")

                trig = await asyncio.to_thread(trigger_update)

                if trig["success"] and ADMIN_CHAT:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_CHAT,
                            text=(
                                f"🔄 *Auto-Update Terdeteksi!*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📦 Dari : `{current}` → `{latest}`\n"
                                f"📝 {info.get('message', '-')}\n"
                                f"👤 {info.get('author', '-')} · {info.get('date', '-')}\n\n"
                                f"🚀 Bot restart otomatis dalam ~30 detik."
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.error(f"Gagal notif auto-update: {e}")
                elif not trig["success"]:
                    logger.warning(f"Auto-update trigger gagal: {trig.get('error')}")

        except Exception as e:
            logger.error(f"Auto-update loop error: {e}")

        await asyncio.sleep(1800)  # Cek tiap 30 menit


async def cmd_testcpanel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnostik lengkap: DNS + panel + koneksi."""
    from cpanel_generator import check_dns, test_connection as cpanel_test_conn, CPANEL_DOMAIN, is_configured

    msg = await update.message.reply_text("🔍 Menjalankan diagnostik cPanel...")

    if not is_configured():
        await msg.edit_text(
            "⚠️ *cPanel belum dikonfigurasi*\n\nSet secrets: `CPANEL_USER`, `CPANEL_PASS`, `CPANEL_DOMAIN`",
            parse_mode="Markdown",
        )
        return

    # 1. DNS check
    dns = await asyncio.to_thread(check_dns, CPANEL_DOMAIN)
    d   = dns["details"]

    domain_icon = "✅" if d["domain"]["resolved"] else "❌"
    mail_icon   = "✅" if d["mail_host"]["resolved"] else "❌"
    dns_icon    = "✅" if dns["dns_ready"] else "⏳"

    domain_ip = d["domain"]["ip"] or "Belum resolve"
    mail_ip   = d["mail_host"]["ip"] or "Belum resolve"

    # 2. Panel connection test
    conn = await asyncio.to_thread(cpanel_test_conn)
    panel_icon = "✅" if conn.get("success") else "❌"
    panel_err  = conn.get("error", "OK")

    text = (
        f"🔍 *Diagnostik cPanel / InfinityFree*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 *Domain:* `{CPANEL_DOMAIN}`\n"
        f"🔧 *Backend:* {conn.get('backend', 'Unknown')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*📡 Status DNS:*\n"
        f"{dns_icon} DNS Siap: {'Ya' if dns['dns_ready'] else 'BELUM (masih propagating)'}\n"
        f"{domain_icon} Domain → `{domain_ip}`\n"
        f"{mail_icon} Mail host → `{mail_ip}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*🔌 Koneksi Panel:*\n"
        f"{panel_icon} Panel login: {'Berhasil' if conn.get('success') else panel_err}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if not dns["dns_ready"]:
        text += (
            f"⏳ *DNS belum siap — apa yang harus dilakukan?*\n"
            f"• InfinityFree butuh hingga *72 jam* untuk propagasi DNS\n"
            f"• Ini normal untuk domain baru\n"
            f"• Coba `/testcpanel` lagi dalam beberapa jam\n"
            f"• Sementara itu, pakai `/addsmtp` dengan Gmail App Password"
        )
    elif not conn.get("success"):
        from cpanel_generator import CPANEL_USER as _cu
        user_hint = ""
        if "@" not in _cu:
            user_hint = (
                f"\n• ⚠️ `CPANEL_USER` = `{_cu}` — ini username hosting, bukan email\\!\n"
                f"• Ganti `CPANEL_USER` di GitHub Secrets dengan **email** yang kamu\n"
                f"  pakai daftar di infinityfree\\.net \\(contoh: `user@gmail\\.com`\\)"
            )
        text += (
            f"⚠️ *DNS sudah ready tapi panel gagal*\n"
            f"• Error: {panel_err}\n"
            f"{user_hint}\n"
            f"• Setelah ubah secret → restart workflow di GitHub Actions"
        )
    else:
        text += "✅ *Semua siap\\! Coba `/cpanelgen` sekarang\\.*"

    await msg.edit_text(text, parse_mode="Markdown")


async def cmd_cpanel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 *Panduan Setup Hosting Gratis \\(InfinityFree\\)*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "*Langkah 1: Daftar InfinityFree*\n"
        "👉 infinityfree\\.net → Sign Up \\(gratis\\)\n\n"
        "*Langkah 2: Buat Hosting Account*\n"
        "• Pilih subdomain bebas, misal: `namakamu\\.epizy\\.com`\n"
        "• Hosting gratis, tidak perlu kartu kredit\n\n"
        "*Langkah 3: Catat Kredensial cPanel*\n"
        "• cPanel URL: `https://cpanel\\.epizy\\.com` \\(atau yang dikasih\\)\n"
        "• Username & password cPanel\n"
        "• Domain: `namakamu\\.epizy\\.com`\n\n"
        "*Langkah 4: Set GitHub Secrets*\n"
        "Buka repo → Settings → Secrets → Actions:\n"
        "```\nCPANEL_URL    = https://cpanel.epizy.com\nCPANEL_USER   = username_cpanel\nCPANEL_PASS   = password_cpanel\nCPANEL_DOMAIN = namakamu.epizy.com\n```\n\n"
        "*Langkah 5: Restart Bot*\n"
        "Actions → 🤖 SMTP Generator Bot → Run workflow\n\n"
        "✅ Bot siap generate SMTP real\\!",
        parse_mode="MarkdownV2",
    )


# ── Callback Query Handler ─────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "cpanel_gen":
        await query.edit_message_text("⏳ Membuat akun email real via cPanel...")
        result = await asyncio.to_thread(cpanel_create)
        if result["success"]:
            text = fmt_cpanel_success(result)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Generate Lagi",    callback_data="cpanel_gen")],
                [InlineKeyboardButton("📂 Lihat Semua Akun", callback_data="cpanel_list")],
                [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
            ])
        else:
            text = fmt_cpanel_fail(result)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔧 Panduan Setup", callback_data="cpanel_setup")],
                [InlineKeyboardButton("🏠 Menu Utama",    callback_data="back_main")],
            ])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    elif data == "cpanel_list":
        result = await asyncio.to_thread(cpanel_list)
        if not result["success"]:
            text = f"❌ {result['error']}"
        elif not result["accounts"]:
            text = f"📭 Belum ada email di cPanel `{result.get('domain', '')}`"
        else:
            lines = [f"📂 *Email di cPanel ({result['domain']})*\n━━━━━━━━━━━━━━━━━━━━"]
            for i, acc in enumerate(result["accounts"], 1):
                email  = acc.get("email", acc.get("login", "?"))
                quota  = acc.get("_diskquota", acc.get("quota", "?"))
                lines.append(f"{i}. `{email}` \\({quota} MB\\)")
            lines.append(f"\nTotal: {result['count']} akun")
            text = "\n".join(lines)
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Generate Baru", callback_data="cpanel_gen")],
                [InlineKeyboardButton("🔙 Menu Utama",   callback_data="back_main")],
            ]),
        )

    elif data == "cpanel_setup":
        await query.edit_message_text(
            "🔧 *Setup Hosting Gratis \\(InfinityFree\\)*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "*1\\.* Daftar di infinityfree\\.net \\(gratis\\)\n"
            "*2\\.* Buat hosting → dapat subdomain `.epizy.com`\n"
            "*3\\.* Catat: cPanel URL, username, password, domain\n"
            "*4\\.* Set 4 GitHub Secrets:\n"
            "`CPANEL_URL` `CPANEL_USER` `CPANEL_PASS` `CPANEL_DOMAIN`\n"
            "*5\\.* Restart bot di GitHub Actions\n\n"
            "Ketik /cpanelsetup untuk panduan lengkap\\.".replace(".", "\\."),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "gen":
        await query.edit_message_text("⏳ Generate email sementara...")
        result = await asyncio.to_thread(generator.generate_random)
        await query.edit_message_text(
            fmt_tempmail(result), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",          callback_data="gen")],
                [InlineKeyboardButton("⚡ Generate SMTP Real",     callback_data="cpanel_gen")],
                [InlineKeyboardButton("🏠 Menu Utama",             callback_data="back_main")],
            ]),
        )

    elif data == "menu_provider":
        await query.edit_message_text(
            "🔧 *Pilih Provider Email Temp:*",
            parse_mode="Markdown", reply_markup=provider_keyboard(),
        )

    elif data.startswith("prov_"):
        provider = data[5:]
        await query.edit_message_text(f"⏳ Generate dari *{provider}*...", parse_mode="Markdown")
        result = await asyncio.to_thread(generator.generate_by_provider, provider)
        await query.edit_message_text(
            fmt_tempmail(result), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",  callback_data=f"prov_{provider}")],
                [InlineKeyboardButton("📋 Ganti Provider", callback_data="menu_provider")],
                [InlineKeyboardButton("🏠 Menu Utama",     callback_data="back_main")],
            ]),
        )

    elif data == "add_smtp_info":
        await query.edit_message_text(
            "➕ *Tambah SMTP Manual*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim di chat:\n"
            "`/addsmtp email@gmail.com|app_password`\n\n"
            "📌 Gmail App Password:\n"
            "myaccount.google.com/apppasswords\n\n"
            "✅ Bot verifikasi koneksi sebelum simpan.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "list_smtp":
        accounts = manager.list_accounts()
        if not accounts:
            text = "📭 *Belum ada akun SMTP manual.*\n\nTambah: `/addsmtp email|password`"
        else:
            lines = ["📂 *Akun SMTP Manual*\n━━━━━━━━━━━━━━━━━━━━"]
            for i, a in enumerate(accounts, 1):
                ok = "✅" if a["verified"] else "⚠️"
                lines.append(f"{i}. {ok} `{a['email']}`\n   🌐 {a['smtp_host']}:{a['smtp_port']}")
            lines.append(f"\nTotal: {len(accounts)}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Tambah Baru", callback_data="add_smtp_info")],
                [InlineKeyboardButton("🔙 Kembali",     callback_data="back_main")],
            ]),
        )

    elif data == "howto":
        await query.edit_message_text(
            "📖 *Perbedaan Mode Generate*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚡ *SMTP Real (cPanel)* — TERBAIK\n"
            "• Email nyata di domain hosting kamu\n"
            "• Bisa KIRIM & TERIMA email\n"
            "• Butuh setup InfinityFree (gratis)\n\n"
            "📧 *Email Temp* — Receive Only\n"
            "• Langsung jadi, tidak perlu setup\n"
            "• Hanya bisa MENERIMA via website\n"
            "• Tidak bisa login SMTP/IMAP\n\n"
            "➕ *SMTP Manual* — Akun Sendiri\n"
            "• Masukkan Gmail + App Password\n"
            "• Diverifikasi sebelum disimpan\n"
            "• Bisa KIRIM & TERIMA",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "status":
        cpanel_ok = cpanel_configured()
        cp_status = f"✅ Aktif (`{CPANEL_DOMAIN}`)" if cpanel_ok else "⚠️ Belum setup"
        await query.edit_message_text(
            f"📊 *Status Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Bot: Online\n"
            f"⏰ {now_utc()}\n"
            f"🆔 Run ID: `{RUN_ID}`\n"
            f"🔧 cPanel: {cp_status}\n"
            f"📧 Provider Temp: {len(generator.list_providers())}\n"
            f"📂 Akun Manual: {manager.count()}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "check_update":
        await query.edit_message_text("🔍 Memeriksa update dari GitHub...")
        result = await asyncio.to_thread(check_for_update)

        if "error" in result and not result.get("update_available"):
            await query.edit_message_text(
                f"❌ *Gagal cek update*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ {result['error']}\n\n"
                f"💡 Pastikan secret `GH_PAT` sudah diset di GitHub Actions.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
                ]]),
            )
            return

        current = result.get("current_sha", "?")
        latest  = result.get("latest_sha", "?")
        info    = result.get("commit_info", {})

        if not result.get("update_available"):
            await query.edit_message_text(
                f"✅ *Bot sudah versi terbaru!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Commit: `{latest}`\n"
                f"📝 {info.get('message', '-')}\n"
                f"👤 {info.get('author', '-')} · {info.get('date', '-')}\n"
                f"⏰ {now_utc()}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
                ]]),
            )
            return

        # Ada update
        trig = await asyncio.to_thread(trigger_update)
        if trig["success"]:
            await query.edit_message_text(
                f"🚀 *Update berhasil di-trigger!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Dari : `{current}` → `{latest}`\n"
                f"📝 {info.get('message', '-')}\n"
                f"👤 {info.get('author', '-')} · {info.get('date', '-')}\n\n"
                f"⏳ Bot restart dalam ~30 detik dengan kode terbaru.",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"❌ *Gagal trigger update*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Ada update: `{current}` → `{latest}`\n"
                f"⚠️ {trig.get('error', 'Unknown')}\n\n"
                f"💡 Cek secret `GH_PAT` punya scope `workflow`.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
                ]]),
            )

    elif data == "fix_info":
        await query.edit_message_text(
            "🔧 *WhatsApp Fix — Banding Ban*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Gunakan command:\n"
            "`/fix +628xxxxxxxx`\n\n"
            "Contoh:\n"
            "`/fix +6281234567890`\n\n"
            "📌 Bot akan:\n"
            "1️⃣ Kirim email banding ke WhatsApp Support\n"
            "2️⃣ Monitor inbox IMAP untuk balasan\n"
            "3️⃣ Notifikasi kamu otomatis saat ada balasan\n\n"
            "💡 Pastikan sudah ada SMTP manual: `/addsmtp`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "back_main":
        cpanel_ok  = cpanel_configured()
        mode_label = f"✅ cPanel: `{CPANEL_DOMAIN}`" if cpanel_ok else "⚠️ cPanel belum setup"
        await query.edit_message_text(
            f"🤖 *SMTP Generator Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔧 {mode_label}\n\n"
            f"Pilih menu:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ── Startup Notification ───────────────────────────────────────────────────────
async def send_startup_notification(bot: Bot):
    if not ADMIN_CHAT:
        return
    cpanel_ok = cpanel_configured()
    cp_info   = f"✅ cPanel aktif (`{CPANEL_DOMAIN}`)" if cpanel_ok else "⚠️ cPanel belum setup"
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT,
            text=(
                "🚀 *Bot SMTP Generator Aktif!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ *Waktu:* {now_utc()}\n"
                f"🆔 *Run ID:* `{RUN_ID}`\n"
                f"📦 *Repo:* `{REPO}`\n"
                f"🔧 {cp_info}\n"
                f"📧 Provider Temp: {len(generator.list_providers())}\n"
                f"📂 Akun Manual: {manager.count()}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ Bot siap menerima perintah!"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Notif startup gagal: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("generate",    cmd_generate))
    app.add_handler(CommandHandler("cpanelgen",   cmd_cpanel_gen))
    app.add_handler(CommandHandler("addsmtp",     cmd_addsmtp))
    app.add_handler(CommandHandler("listsmtp",    cmd_listsmtp))
    app.add_handler(CommandHandler("delsmtp",     cmd_delsmtp))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("cpanelsetup", cmd_cpanel_setup))
    app.add_handler(CommandHandler("testcpanel",  cmd_testcpanel))
    app.add_handler(CommandHandler("fix",         cmd_fix))
    app.add_handler(CommandHandler("update",      cmd_update))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def post_init(application: Application):
        await send_startup_notification(application.bot)
        await application.bot.set_my_commands([
            BotCommand("start",       "Menu utama"),
            BotCommand("cpanelgen",   "⚡ Generate SMTP real (cPanel)"),
            BotCommand("generate",    "📧 Email sementara (receive only)"),
            BotCommand("addsmtp",     "➕ Tambah SMTP manual"),
            BotCommand("listsmtp",    "📂 Lihat akun SMTP"),
            BotCommand("delsmtp",     "🗑 Hapus akun SMTP"),
            BotCommand("fix",         "🔧 Banding ban WhatsApp"),
            BotCommand("update",      "🔄 Cek & update bot dari GitHub"),
            BotCommand("cpanelsetup", "🔧 Panduan setup hosting gratis"),
            BotCommand("testcpanel",  "🔍 Diagnostik DNS + panel"),
            BotCommand("status",      "📊 Status bot"),
            BotCommand("help",        "❓ Bantuan"),
        ])
        # Mulai background loops
        asyncio.create_task(imap_monitor_loop(application.bot))
        asyncio.create_task(auto_update_loop(application.bot))

    app.post_init = post_init
    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
