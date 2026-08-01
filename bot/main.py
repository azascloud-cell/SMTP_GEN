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
    MessageHandler,
    filters,
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
from number_manager import (
    parse_numbers_from_text,
    pick_random,
    check_numbers,
    is_checker_connected,
    status_emoji,
    status_label,
    WA_CHECKER_URL,
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

# ── Simpan daftar nomor per user sementara (in-memory) ───────────────────────
# { chat_id: ["+628...", ...] }
_user_numbers: dict[int, list[str]] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main_menu_keyboard():
    rows = []
    # Nomor Management
    rows.append([InlineKeyboardButton("📱 Cek Nomor WA (Upload .txt)", callback_data="num_info")])
    checker_label = "🔗 WA Checker: Terhubung ✅" if is_checker_connected() else "🔗 Connect WA Checker"
    rows.append([InlineKeyboardButton(checker_label, callback_data="connect_wa_info")])
    # Email temp
    rows.append([InlineKeyboardButton("📧 Email Temp (Receive Only)",      callback_data="gen")])
    rows.append([InlineKeyboardButton("📋 Pilih Provider Email Temp",      callback_data="menu_provider")])
    # SMTP Manual
    rows.append([InlineKeyboardButton("➕ Tambah SMTP Manual",              callback_data="add_smtp_info")])
    rows.append([InlineKeyboardButton("📂 Akun SMTP Manual",               callback_data="list_smtp")])
    # Mailtrap & Mailpit
    rows.append([
        InlineKeyboardButton("📬 Mailtrap SMTP", callback_data="mailtrap_info"),
        InlineKeyboardButton("🔩 Mailpit SMTP",  callback_data="mailpit_info"),
    ])
    # WhatsApp Fix
    rows.append([InlineKeyboardButton("🔧 WhatsApp Fix (/fix +nomor)",     callback_data="fix_info")])
    # Update & Info
    rows.append([InlineKeyboardButton("🔄 Cek & Update Bot",               callback_data="check_update")])
    rows.append([InlineKeyboardButton("❓ Cara Pakai",  callback_data="howto"),
                 InlineKeyboardButton("📊 Status Bot",  callback_data="status")])
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
def fmt_tempmail(result: dict) -> str:
    if not result["success"]:
        return f"❌ *Gagal generate:* {result.get('error', 'Unknown')}"
    d = result["data"]
    provider = d.get("provider", "")
    email    = d["email"]
    expires  = d.get("expires", "-")

    # Tentukan cara cek inbox berdasarkan provider (tanpa link yang bisa ditekan)
    if provider == "Mail.tm":
        inbox_note = "Login manual di *mail.tm* → masukkan email & password di atas"
    elif provider == "GuerrillaMail":
        inbox_note = "Buka *guerrillamail.com* → cek inbox di sana"
    elif provider == "1SecMail":
        inbox_note = "Buka *1secmail.com* → masukkan email untuk cek inbox"
    else:
        inbox_note = d.get("note", "Buka website provider untuk cek inbox")

    return (
        f"📧 *Email Sementara*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 *Email:* `{email}`\n"
        f"🔑 *Password:* `{d.get('password', '-')}`\n"
        f"⏱ *Expires:* {expires}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 *Cara cek inbox:*\n"
        f"{inbox_note}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ *Ini hanya untuk MENERIMA email*\n"
        f"Untuk kirim email, gunakan menu ➕ *SMTP Manual*"
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


def fmt_number_results(results: list[dict], total_in_file: int) -> str:
    lines = [
        f"📱 *Hasil Cek Nomor WhatsApp*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📄 Total nomor di file: {total_in_file}",
        f"🎲 Dipilih acak: {len(results)} nomor",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in results:
        icon = status_emoji(r["registered"])
        lbl  = status_label(r["registered"])
        lines.append(f"{icon} `{r['phone']}` — {lbl}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    if not is_checker_connected():
        lines.append("⚠️ WA Checker belum terhubung — status tidak bisa dicek")
        lines.append("Tekan *Connect WA Checker* untuk konfigurasi")
    else:
        registered = sum(1 for r in results if r["registered"] is True)
        fresh      = sum(1 for r in results if r["registered"] is False)
        lines.append(f"🟢 Fresh (belum WA): {fresh}  |  🔴 Terdaftar WA: {registered}")
    return "\n".join(lines)


def build_number_buttons(results: list[dict], chat_id: int) -> InlineKeyboardMarkup:
    """
    Buat keyboard:
    - Tiap nomor punya tombol copy → callback "copy_NUM"
      🟢 = fresh (belum WA), 🔴 = terdaftar WA, ⚪ = tidak diketahui
    - Tombol 🔄 Acak Lagi dan 🏠 Menu
    """
    rows = []
    for r in results:
        icon  = status_emoji(r["registered"])
        phone = r["phone"]
        rows.append([InlineKeyboardButton(
            f"{icon} 📋 {phone}",
            callback_data=f"copy_num_{phone}",
        )])
    rows.append([
        InlineKeyboardButton("🔄 Acak Lagi",  callback_data="num_reroll"),
        InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Command Handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    checker_status = "✅ WA Checker terhubung" if is_checker_connected() else "⚠️ WA Checker belum diset"
    await update.message.reply_text(
        f"🤖 *Bot Management Nomor & SMTP*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 {checker_status}\n\n"
        f"📱 *Cek Nomor WA* — upload file .txt berisi nomor\n"
        f"📧 *Email Temp* — hanya untuk menerima \\(web only\\)\n"
        f"➕ *SMTP Manual* — tambah Gmail/Yahoo dengan App Password\n"
        f"🔧 *WA Fix* — banding ban WhatsApp\n\n"
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
            [InlineKeyboardButton("🔄 Generate Lagi", callback_data="gen")],
            [InlineKeyboardButton("🏠 Menu Utama",    callback_data="back_main")],
        ]),
    )


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


async def cmd_addmailtrap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tambah Mailtrap SMTP. Format: /addmailtrap username|password"""
    args_raw = " ".join(context.args).strip() if context.args else ""
    if "|" not in args_raw:
        await update.message.reply_text(
            "📬 *Tambah Mailtrap SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addmailtrap username|password`\n\n"
            "📌 Cara dapat credentials:\n"
            "1️⃣ Buka *mailtrap.io* → Login\n"
            "2️⃣ Pilih Inboxes → pilih inbox\n"
            "3️⃣ Tab *SMTP Settings*\n"
            "4️⃣ Salin *Username* dan *Password*\n\n"
            "Contoh:\n"
            "`/addmailtrap abc123def456|xyz789ghi012`",
            parse_mode="Markdown",
        )
        return
    parts    = args_raw.split("|", 1)
    username = parts[0].strip()
    password = parts[1].strip()
    if not username or not password:
        await update.message.reply_text("⚠️ Format: `/addmailtrap username|password`", parse_mode="Markdown")
        return

    msg = await update.message.reply_text(
        f"🔄 Verifikasi Mailtrap `{username}`...", parse_mode="Markdown"
    )
    result = await asyncio.to_thread(manager.add_mailtrap, username, password)
    text   = fmt_smtp_add_ok(result) if result["success"] else fmt_smtp_add_fail(result)
    kb     = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Lihat Akun", callback_data="list_smtp")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
    ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_addmailpit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tambah Mailpit SMTP. Format: /addmailpit host:port  atau  /addmailpit host:port|user|pass"""
    args_raw = " ".join(context.args).strip() if context.args else ""
    if not args_raw:
        await update.message.reply_text(
            "🔩 *Tambah Mailpit SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format tanpa auth:\n"
            "`/addmailpit host:port`\n\n"
            "Format dengan auth:\n"
            "`/addmailpit host:port|username|password`\n\n"
            "Contoh:\n"
            "`/addmailpit mail.example.com:1025`\n"
            "`/addmailpit mail.example.com:1025|user|pass`\n\n"
            "💡 Default Mailpit: port 1025, tanpa auth.",
            parse_mode="Markdown",
        )
        return

    parts    = args_raw.split("|")
    host_port = parts[0].strip()
    username  = parts[1].strip() if len(parts) > 1 else ""
    password  = parts[2].strip() if len(parts) > 2 else ""

    if ":" in host_port:
        host_str, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            await update.message.reply_text("⚠️ Port tidak valid. Contoh: `mail.example.com:1025`", parse_mode="Markdown")
            return
    else:
        host_str = host_port
        port     = 1025

    msg = await update.message.reply_text(
        f"🔄 Verifikasi Mailpit `{host_str}:{port}`...", parse_mode="Markdown"
    )
    result = await asyncio.to_thread(manager.add_mailpit, host_str, port, username, password)
    text   = fmt_smtp_add_ok(result) if result["success"] else fmt_smtp_add_fail(result)
    kb     = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Lihat Akun", callback_data="list_smtp")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
    ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


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
    checker_ok = is_checker_connected()
    ch_status  = f"✅ Terhubung (`{WA_CHECKER_URL}`)" if checker_ok else "⚠️ Belum setup"
    await update.message.reply_text(
        f"📊 *Status Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 Bot: Online\n"
        f"⏰ Waktu: {now_utc()}\n"
        f"🆔 Run ID: `{RUN_ID}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 WA Checker: {ch_status}\n"
        f"📧 Provider Temp: {len(generator.list_providers())}\n"
        f"📂 Akun Manual: {manager.count()}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Panduan Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📱 *Cek Nomor WA*\n"
        "Upload file `.txt` \\(satu nomor per baris\\)\n"
        "Bot pilih 3 nomor acak & cek status WA\n"
        "🟢 Hijau = fresh, 🔴 Merah = terdaftar WA\n\n"
        "📧 *Email Temp* \\(Receive Only\\)\n"
        "Langsung generate, tapi hanya untuk menerima\n\n"
        "➕ *SMTP Manual* \\(Gmail/Yahoo\\)\n"
        "`/addsmtp email|app\\_password`\n\n"
        "📬 *Mailtrap SMTP* \\(sandbox testing\\)\n"
        "`/addmailtrap username|password`\n"
        "Credentials dari mailtrap\\.io → Inboxes → SMTP Settings\n\n"
        "🔩 *Mailpit SMTP* \\(self\\-hosted\\)\n"
        "`/addmailpit host:port`\n"
        "`/addmailpit host:port|user|pass`\n\n"
        "🔧 *WhatsApp Fix*\n"
        "`/fix \\+628xxxxxxxx`\n"
        "Kirim email banding ke WhatsApp support\n\n"
        "📋 *Commands:*\n"
        "/start — Menu utama\n"
        "/generate — Email temp\n"
        "/addsmtp — Tambah SMTP Gmail/Yahoo\n"
        "/addmailtrap — Tambah Mailtrap SMTP\n"
        "/addmailpit — Tambah Mailpit SMTP\n"
        "/listsmtp — Lihat akun SMTP\n"
        "/delsmtp — Hapus akun SMTP\n"
        "/fix — Banding ban WhatsApp\n"
        "/status — Status bot",
        parse_mode="MarkdownV2",
    )


# ── Number Management Document Handler ────────────────────────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tangani upload file .txt untuk cek nomor WA."""
    doc = update.message.document
    if not doc:
        return

    # Hanya proses file .txt
    fname = doc.file_name or ""
    if not fname.lower().endswith(".txt"):
        await update.message.reply_text(
            "⚠️ Hanya file *.txt* yang didukung untuk cek nomor.\n"
            "Format: satu nomor per baris.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text(
        f"📄 Memproses *{fname}*...",
        parse_mode="Markdown",
    )

    try:
        tg_file  = await doc.get_file()
        content  = await tg_file.download_as_bytearray()
        text     = content.decode("utf-8", errors="replace")
    except Exception as e:
        await msg.edit_text(f"❌ Gagal baca file: {e}")
        return

    numbers = parse_numbers_from_text(text)
    if not numbers:
        await msg.edit_text(
            f"❌ *Tidak ada nomor valid di file `{fname}`*\n\n"
            f"Pastikan format: satu nomor per baris\\.",
            parse_mode="MarkdownV2",
        )
        return

    # Simpan daftar nomor untuk reroll
    chat_id = update.effective_chat.id
    _user_numbers[chat_id] = numbers

    # Pilih 3 acak & cek WA
    chosen  = pick_random(numbers, 3)
    await msg.edit_text(
        f"🔍 Mengecek {len(chosen)} nomor dari {len(numbers)} di file...",
    )
    results = await asyncio.to_thread(check_numbers, chosen)

    text_out = fmt_number_results(results, len(numbers))
    kb       = build_number_buttons(results, chat_id)
    await msg.edit_text(text_out, parse_mode="Markdown", reply_markup=kb)


# ── WhatsApp Fix Command ───────────────────────────────────────────────────────
async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

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
    smtp_full  = manager.get_account(smtp_email)
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
                    chat_id     = item["chat_id"]
                    phone       = item["phone"]
                    smtp_em     = item["smtp_email"]
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

        await asyncio.sleep(300)


# ── GitHub Auto-Update ────────────────────────────────────────────────────────
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    logger.info("Auto-update loop dimulai...")
    current_sha = os.environ.get("GITHUB_SHA", "")
    if current_sha:
        await asyncio.to_thread(save_commit, current_sha)
    await asyncio.sleep(300)

    while True:
        try:
            result = await asyncio.to_thread(check_for_update)
            if result.get("update_available"):
                latest  = result.get("latest_sha", "?")
                current = result.get("current_sha", "?")
                info    = result.get("commit_info", {})
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

        await asyncio.sleep(1800)


# ── Callback Query Handler ─────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    chat_id = query.message.chat_id

    # ── Number Management ─────────────────────────────────────────────────────
    if data == "num_info":
        await query.edit_message_text(
            "📱 *Cek Nomor WhatsApp*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Cara pakai:\n"
            "1️⃣ Kirim file `.txt` ke bot ini\n"
            "2️⃣ Format: satu nomor per baris\n"
            "3️⃣ Bot pilih 3 nomor acak\n"
            "4️⃣ Cek status WA tiap nomor\n\n"
            "🟢 *Hijau* = Fresh \\(belum terdaftar WA\\)\n"
            "🔴 *Merah* = Sudah terdaftar WA\n\n"
            "📄 *Contoh isi file:*\n"
            "`+6281234567890`\n"
            "`+6285678901234`\n"
            "`+6287890123456`\n\n"
            "⬆️ Upload file .txt sekarang\\!",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "connect_wa_info":
        checker_ok = is_checker_connected()
        if checker_ok:
            status_text = (
                f"✅ *WA Checker Terhubung*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 URL: `{WA_CHECKER_URL}`\n\n"
                f"Bot siap mengecek nomor WhatsApp!"
            )
        else:
            status_text = (
                "🔗 *Connect WA Checker*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Untuk cek status WA nomor, kamu perlu menyiapkan checker sendiri.\n\n"
                "*Cara Setup:*\n"
                "1\\. Siapkan server WA checker \\(contoh: Baileys/WA-JS\\)\n"
                "2\\. Expose endpoint: `GET /check?phone=+628xxx`\n"
                "   Response: `{\"registered\": true/false}`\n"
                "3\\. Set env var di GitHub Secrets:\n"
                "   `WA_CHECKER_URL = https://checker-kamu.example.com`\n"
                "4\\. Restart bot\n\n"
                "⚠️ Tanpa checker, status nomor tidak bisa diketahui\\.\n"
                "Bot tetap bisa memilih & menampilkan nomor dari file\\."
            )
        await query.edit_message_text(
            status_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
            ]]),
        )

    elif data == "num_reroll":
        numbers = _user_numbers.get(chat_id, [])
        if not numbers:
            await query.edit_message_text(
                "⚠️ Tidak ada daftar nomor tersimpan.\nUpload ulang file .txt kamu.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")
                ]]),
            )
            return

        await query.edit_message_text("🎲 Mengambil nomor acak baru...")
        chosen  = pick_random(numbers, 3)
        results = await asyncio.to_thread(check_numbers, chosen)
        text_out = fmt_number_results(results, len(numbers))
        kb       = build_number_buttons(results, chat_id)
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("copy_num_"):
        phone = data[len("copy_num_"):]
        await query.answer(f"Nomor: {phone}", show_alert=True)

    # ── Email Temp ────────────────────────────────────────────────────────────
    elif data == "gen":
        await query.edit_message_text("⏳ Generate email sementara...")
        result = await asyncio.to_thread(generator.generate_random)
        await query.edit_message_text(
            fmt_tempmail(result), parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Generate Lagi",  callback_data="gen")],
                [InlineKeyboardButton("🏠 Menu Utama",     callback_data="back_main")],
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

    # ── SMTP Manual ───────────────────────────────────────────────────────────
    elif data == "mailtrap_info":
        await query.edit_message_text(
            "📬 *Mailtrap SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Mailtrap adalah layanan **testing SMTP** gratis.\n"
            "Email yang dikirim ditangkap di inbox sandbox, tidak diteruskan ke tujuan asli.\n\n"
            "📌 *Cara dapat credentials:*\n"
            "1️⃣ Daftar/login di *mailtrap.io*\n"
            "2️⃣ Buka menu *Email Testing → Inboxes*\n"
            "3️⃣ Klik inbox kamu → tab *SMTP Settings*\n"
            "4️⃣ Pilih integrasi *Python / SMTP*\n"
            "5️⃣ Salin *Username* dan *Password*\n\n"
            "🔧 *Lalu kirim di chat:*\n"
            "`/addmailtrap username|password`\n\n"
            "📋 *Detail Server Mailtrap:*\n"
            "Host: `sandbox.smtp.mailtrap.io`\n"
            "Port: `2525` (atau 465, 587)\n"
            "Enkripsi: STARTTLS / SSL",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Kembali", callback_data="back_main")],
            ]),
        )

    elif data == "mailpit_info":
        await query.edit_message_text(
            "🔩 *Mailpit SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Mailpit adalah email catcher **self-hosted** (seperti MailHog).\n"
            "Email ditangkap di web UI Mailpit, tidak dikirim ke server asli.\n\n"
            "📌 *Setup Mailpit (server kamu):*\n"
            "• Download: `github.com/axllent/mailpit`\n"
            "• Jalankan: `./mailpit`\n"
            "• Default SMTP port: *1025*\n"
            "• Default web UI: `http://0.0.0.0:8025`\n\n"
            "🔧 *Tambah tanpa auth:*\n"
            "`/addmailpit host:port`\n\n"
            "🔧 *Tambah dengan auth:*\n"
            "`/addmailpit host:port|username|password`\n\n"
            "Contoh:\n"
            "`/addmailpit mail.example.com:1025`\n"
            "`/addmailpit 192.168.1.10:1025|admin|secret`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Kembali", callback_data="back_main")],
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
            "📖 *Panduan Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📱 *Cek Nomor WA*\n"
            "• Upload file `.txt` berisi nomor\n"
            "• Bot pilih 3 acak & cek status WA\n"
            "• 🟢 Hijau = fresh, 🔴 Merah = terdaftar WA\n\n"
            "📧 *Email Temp* — Receive Only\n"
            "• Langsung jadi, tidak perlu setup\n"
            "• Hanya bisa MENERIMA via website\n\n"
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
        checker_ok = is_checker_connected()
        ch_status  = f"✅ Terhubung" if checker_ok else "⚠️ Belum setup"
        await query.edit_message_text(
            f"📊 *Status Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Bot: Online\n"
            f"⏰ {now_utc()}\n"
            f"🆔 Run ID: `{RUN_ID}`\n"
            f"📱 WA Checker: {ch_status}\n"
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
        checker_ok = is_checker_connected()
        ch_label   = "✅ WA Checker terhubung" if checker_ok else "⚠️ WA Checker belum diset"
        await query.edit_message_text(
            f"🤖 *Bot Management Nomor & SMTP*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 {ch_label}\n\n"
            f"Pilih menu:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ── Startup Notification ───────────────────────────────────────────────────────
async def send_startup_notification(bot: Bot):
    if not ADMIN_CHAT:
        return
    checker_ok = is_checker_connected()
    ch_info    = f"✅ WA Checker: `{WA_CHECKER_URL}`" if checker_ok else "⚠️ WA Checker belum setup"
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT,
            text=(
                "🚀 *Bot Management Nomor & SMTP Aktif!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ *Waktu:* {now_utc()}\n"
                f"🆔 *Run ID:* `{RUN_ID}`\n"
                f"📦 *Repo:* `{REPO}`\n"
                f"📱 {ch_info}\n"
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
    app.add_handler(CommandHandler("addsmtp",     cmd_addsmtp))
    app.add_handler(CommandHandler("listsmtp",    cmd_listsmtp))
    app.add_handler(CommandHandler("delsmtp",     cmd_delsmtp))
    app.add_handler(CommandHandler("addmailtrap", cmd_addmailtrap))
    app.add_handler(CommandHandler("addmailpit",  cmd_addmailpit))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("fix",         cmd_fix))
    app.add_handler(CommandHandler("update",      cmd_update))
    app.add_handler(CallbackQueryHandler(button_handler))
    # Handler untuk upload file .txt
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_document))

    async def post_init(application: Application):
        await send_startup_notification(application.bot)
        await application.bot.set_my_commands([
            BotCommand("start",       "Menu utama"),
            BotCommand("generate",    "📧 Email sementara (receive only)"),
            BotCommand("addsmtp",     "➕ Tambah SMTP manual (Gmail/Yahoo)"),
            BotCommand("addmailtrap", "📬 Tambah Mailtrap SMTP (testing)"),
            BotCommand("addmailpit",  "🔩 Tambah Mailpit SMTP (self-hosted)"),
            BotCommand("listsmtp",    "📂 Lihat semua akun SMTP"),
            BotCommand("delsmtp",     "🗑 Hapus akun SMTP"),
            BotCommand("fix",         "🔧 Banding ban WhatsApp"),
            BotCommand("update",      "🔄 Cek & update bot dari GitHub"),
            BotCommand("status",      "📊 Status bot"),
            BotCommand("help",        "❓ Bantuan"),
        ])
        asyncio.create_task(imap_monitor_loop(application.bot))
        asyncio.create_task(auto_update_loop(application.bot))

    app.post_init = post_init
    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
