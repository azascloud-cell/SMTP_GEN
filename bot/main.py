import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from github_updater import (
    check_for_update,
    save_commit,
    trigger_update,
)
from number_files import delete_file, detect_region, list_files, load_file, save_file
from number_manager import (
    WA_CHECKER_URL,
    check_numbers,
    is_checker_connected,
    parse_numbers_from_text,
    pick_random,
    status_emoji,
    status_label,
)
from smtp_auto_generator import MAILTRAP_API_TOKEN, auto_gen_smtp
from smtp_generator import SMTPGenerator
from smtp_manager import SMTPManager
from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from whatsapp_fix import (
    add_pending,
    check_whatsapp_reply,
    get_all_pending,
    increment_check,
    mark_notified,
    send_appeal_email,
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

# ── Simpan nama file terakhir yang digunakan per user ─────────────────────────
# Dipakai oleh num_reroll agar bisa reload dari GitHub setelah restart
# { chat_id: "filename.txt" }
_last_file_by_chat: dict[int, str] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main_menu_keyboard():
    rows = []
    # Nomor Management
    rows.append([InlineKeyboardButton("📱 Cek Nomor WA",               callback_data="num_info")])
    checker_label = "🔗 WA Checker: Terhubung ✅" if is_checker_connected() else "🔗 Connect WA Checker"
    rows.append([InlineKeyboardButton(checker_label, callback_data="connect_wa_info")])
    # Email temp
    rows.append([InlineKeyboardButton("📧 Email Temp (Receive Only)",      callback_data="gen")])
    rows.append([InlineKeyboardButton("📋 Pilih Provider Email Temp",      callback_data="menu_provider")])
    # SMTP Auto & Manual
    rows.append([InlineKeyboardButton("🤖 Auto Generate SMTP",             callback_data="autogen_smtp")])
    rows.append([InlineKeyboardButton("➕ Tambah SMTP Manual",              callback_data="add_smtp_info")])
    rows.append([InlineKeyboardButton("📂 Akun SMTP",                      callback_data="list_smtp")])
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
        "📱 *Hasil Cek Nomor WhatsApp*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📄 Total nomor di file: {total_in_file}",
        f"🎲 Dipilih acak: {len(results)} nomor",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for r in results:
        icon = status_emoji(r["registered"])
        lbl  = status_label(r["registered"])
        lines.append(f"{icon} `{r['phone']}` — {lbl}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
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
        f"📧 *Email Temp* — generate & cek inbox langsung dari bot\n"
        f"➕ *SMTP Manual* — tambah Gmail/Yahoo dengan App Password\n"
        f"🔧 *WA Fix* — banding ban WhatsApp\n\n"
        f"Pilih menu di bawah:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate email temp (receive-only)."""
    msg = await update.message.reply_text("⏳ Generate email sementara...")
    result = await asyncio.to_thread(generator.generate_random)
    if result["success"]:
        context.user_data["last_temp_email"] = result["data"]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Cek Inbox", callback_data="check_inbox_temp")],
            [InlineKeyboardButton("🔄 Generate Lagi", callback_data="gen")],
            [InlineKeyboardButton("🏠 Menu Utama",    callback_data="back_main")],
        ])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]])
    await msg.edit_text(
        fmt_tempmail(result), parse_mode="Markdown",
        reply_markup=kb,
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
    key = " ".join(context.args).strip() if context.args else ""
    if not key:
        # Tampilkan semua akun agar user tahu key yang benar
        accounts = manager.list_accounts()
        if not accounts:
            await update.message.reply_text(
                "📭 Belum ada akun SMTP.\n\nFormat: `/delsmtp key`",
                parse_mode="Markdown",
            )
            return
        lines = ["🗑 *Hapus Akun SMTP*\n━━━━━━━━━━━━━━━━━━━━\nKirim key yang ingin dihapus:\n"]
        for a in accounts:
            lines.append(f"• `{a['email']}`")
        lines.append("\nContoh:\n`/delsmtp email@gmail.com`\n`/delsmtp mailtrap:usernamenya`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    result = manager.remove_account(key)
    if result["success"]:
        await update.message.reply_text(
            f"✅ `{key}` berhasil dihapus.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📂 Lihat Akun", callback_data="list_smtp"),
            ]]),
        )
    else:
        # Coba fuzzy match — tampilkan daftar akun yang ada
        accounts = manager.list_accounts()
        lines = [f"❌ *Key tidak ditemukan:* `{key}`\n\n📋 *Akun yang tersedia:*"]
        for a in accounts:
            lines.append(f"• `{a['email']}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
        "Upload file `.txt` (satu nomor per baris)\n"
        "Bot pilih 3 nomor acak & cek status WA\n"
        "🟢 Hijau = fresh, 🔴 Merah = terdaftar WA\n\n"
        "📧 *Email Temp* (Inbox Terintegrasi)\n"
        "Langsung generate & cek inbox langsung di dalam bot!\n\n"
        "➕ *SMTP Manual* (Gmail/Yahoo)\n"
        "`/addsmtp email|app_password`\n\n"
        "📬 *Mailtrap SMTP* (sandbox testing)\n"
        "`/addmailtrap username|password`\n"
        "Credentials dari mailtrap.io → Inboxes → SMTP Settings\n\n"
        "🔩 *Mailpit SMTP* (self-hosted)\n"
        "`/addmailpit host:port`\n"
        "`/addmailpit host:port|user|pass`\n\n"
        "🔧 *WhatsApp Fix*\n"
        "`/fix +628xxxxxxxx`\n"
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
        parse_mode="Markdown",
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
            f"❌ Tidak ada nomor valid di file `{fname}`\n\n"
            f"Pastikan format: satu nomor per baris.",
            parse_mode="Markdown",
        )
        return

    chat_id = update.effective_chat.id
    _user_numbers[chat_id] = numbers

    # Simpan file ke GitHub storage (non-blocking, background)
    safe_fname = fname.replace("_", "\\_")
    await msg.edit_text(
        f"💾 Menyimpan *{safe_fname}* \\({len(numbers)} nomor\\)\\.\\.\\.",
        parse_mode="MarkdownV2",
    )
    sanitized_fname = re.sub(r"[^\w\-.]", "_", fname)
    if not sanitized_fname.lower().endswith(".txt"):
        sanitized_fname += ".txt"
    sanitized_fname = sanitized_fname[:80]
    _last_file_by_chat[chat_id] = sanitized_fname
    asyncio.create_task(asyncio.to_thread(save_file, fname, text, chat_id, len(numbers)))

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
async def cmd_autogen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-generate SMTP via backend (Mail.tm atau Mailtrap)."""
    arg = " ".join(context.args).strip().lower() if context.args else "auto"
    msg = await update.message.reply_text(
        f"⏳ *Auto-generate SMTP...*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Provider: `{arg}`\n"
        f"Mohon tunggu...",
        parse_mode="Markdown",
    )
    result = await asyncio.to_thread(auto_gen_smtp, arg)
    if not result["success"]:
        await msg.edit_text(
            f"❌ *Generate SMTP Gagal*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ {result.get('error', 'Unknown error')}\n\n"
            f"💡 Coba provider lain: /autogen mailtm",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Menu", callback_data="back_main")
            ]]),
        )
        return
    save_result = await asyncio.to_thread(manager.add_auto_generated, result)
    if not save_result["success"]:
        await msg.edit_text(
            f"❌ *Gagal simpan akun SMTP*\n{save_result.get('error', '')}",
            parse_mode="Markdown",
        )
        return
    key = save_result.get("email", result.get("key", "-"))
    await msg.edit_text(
        f"✅ *SMTP Auto-Generate Berhasil!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔖 Provider : `{result.get('provider', '-')}`\n"
        f"👤 Key/User : `{key}`\n"
        f"🔑 Password : `{result.get('password', '-')}`\n"
        f"📤 SMTP     : `{result.get('smtp_host')}:{result.get('smtp_port')}`\n"
        f"📥 IMAP     : `{result.get('imap_host')}:{result.get('imap_port')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {result.get('note', '')}\n\n"
        f"💾 Tersimpan otomatis & siap digunakan!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Lihat Akun SMTP", callback_data="list_smtp")],
            [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
        ]),
    )


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
            "💡 Pastikan ada akun SMTP manual dulu: `/addsmtp`\n"
            "⚠️ Catatan: Akun auto-generated dari temporary email (seperti Mail.tm) tidak diizinkan mengirim email ke luar domain oleh provider, silakan gunakan SMTP manual (Gmail/Yahoo/cPanel) untuk kirim banding.",
            parse_mode="Markdown",
        )
        return

    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    accounts = manager.list_accounts()
    # Filter out auto generated emails because they can't send external email
    verified = [a for a in accounts if a.get("verified") and not a.get("email", "").endswith("@web-library.net") and "mail.tm" not in a.get("email", "")]
    if not verified:
        await update.message.reply_text(
            "❌ *Tidak ada akun SMTP yang aktif!*\n\n"
            "Tambah dulu dengan:\n"
            "`/addsmtp email@gmail.com|app_password`\n\n"
            "📌 Gmail App Password:\n"
            "myaccount.google.com/apppasswords\n\n"
            "⚠️ Catatan: Akun SMTP otomatis (seperti Mail.tm) tidak bisa dipakai kirim email banding.",
            parse_mode="Markdown",
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
    add_pending(chat_id, phone, smtp_email, sent_at)

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

                check_count = item.get("check_count", 0)
                increment_check(key)

                reply = await asyncio.to_thread(
                    check_whatsapp_reply, smtp_full, item["sent_at"]
                )

                chat_id = item["chat_id"]
                phone   = item["phone"]
                smtp_em = item["smtp_email"]

                if reply:
                    body_preview = reply.get("body", "")[:400]
                    confirmed    = reply.get("confirmed", True)

                    if confirmed:
                        header = "📬 *EMAIL DIBALAS OLEH WHATSAPP!*"
                        info   = (
                            "👋 Kabar gembira! Email banding kamu sudah dibalas oleh WhatsApp Support.\n\n"
                            "Kemungkinan besar nomor kamu sudah berhasil diaktifkan kembali.\n"
                            "Coba buka WhatsApp dan cek nomornya ya.\n"
                            "Kalau masih ada kendala, coba banding ulang dengan /fix."
                        )
                    else:
                        header = "📬 *ADA EMAIL MASUK DI INBOX!*"
                        info   = (
                            "⚠️ Ada email masuk di inbox setelah banding dikirim.\n"
                            "Mungkin ini balasan dari WhatsApp, silakan cek manual.\n\n"
                            "Kalau bukan balasan WA, abaikan saja.\n"
                            "Bot akan tetap monitor untuk balasan resmi."
                        )

                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"{header}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📱 Nomor : `{phone}`\n"
                                f"📧 SMTP  : `{smtp_em}`\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{info}\n\n"
                                f"📩 *DETAIL EMAIL*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📧 Dari    : {reply.get('from', '-')}\n"
                                f"📌 Subject : {reply.get('subject', '-')}\n"
                                f"📅 Tanggal : {reply.get('date', '-')}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{body_preview}"
                            ),
                            parse_mode="Markdown",
                        )
                        if confirmed:
                            mark_notified(key)
                        logger.info(f"Notifikasi balasan terkirim ke {chat_id} untuk {phone} (confirmed={confirmed})")
                    except Exception as e:
                        logger.error(f"Gagal kirim notif ke {chat_id}: {e}")

                elif check_count > 0 and check_count % 20 == 0:
                    # Setiap 20 check (~30 menit @ 90 detik/check), kirim status update ke user
                    try:
                        elapsed_min = round(check_count * 1.5)
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🔍 *Monitor Banding Aktif*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📱 Nomor  : `{phone}`\n"
                                f"📧 SMTP   : `{smtp_em}`\n"
                                f"⏱ Elapsed : ±{elapsed_min} menit\n"
                                f"🔄 Dicek   : {check_count + 1}x\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"ℹ️ Belum ada balasan dari WhatsApp.\n"
                                f"Bot tetap monitoring setiap 5 menit.\n"
                                f"Biasanya WhatsApp balas dalam 1–24 jam."
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.error(f"Gagal kirim status update ke {chat_id}: {e}")

        except Exception as e:
            logger.error(f"IMAP monitor error: {e}")

        await asyncio.sleep(90)


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
        # Tampilkan daftar file tersimpan + opsi upload baru
        await query.edit_message_text("⏳ Memuat daftar file...")
        files = await asyncio.to_thread(list_files)

        if not files:
            await query.edit_message_text(
                "📱 *Cek Nomor WhatsApp*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Belum ada file tersimpan.\n\n"
                "⬆️ *Cara pakai:*\n"
                "1️⃣ Kirim file `.txt` ke bot ini\n"
                "2️⃣ Format: satu nomor per baris\n"
                "3️⃣ Bot pilih 3 nomor acak & cek WA\n\n"
                "🟢 Hijau = Fresh (belum WA)\n"
                "🔴 Merah = Sudah terdaftar WA",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="back_main")
                ]]),
            )
        else:
            rows = []
            lines = [
                "📁 *File Manager Nomor WA*",
                "━━━━━━━━━━━━━━━━━━━━",
            ]
            for i, f in enumerate(files[:15], 1):  # max 15 ditampilkan
                fname   = f.get("filename", "?")
                name    = f.get("original_name", fname)[:30]
                total   = f.get("total", "?")
                region  = f.get("region", "") or detect_region(name)
                date_s  = f.get("uploaded_at", "")[:10]  # "YYYY-MM-DD"
                region_tag = f"  {region}" if region else ""
                lines.append(f"{i}. `{name}`{region_tag}")
                lines.append(f"   📊 {total} nomor  📅 {date_s}")
                rows.append([
                    InlineKeyboardButton("✅ Cek Acak", callback_data=f"numfile_pick_{fname}"),
                    InlineKeyboardButton("👁 Lihat",    callback_data=f"numfile_view_{fname}"),
                    InlineKeyboardButton("🗑 Hapus",    callback_data=f"numfile_del_{fname}"),
                ])
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"Total: {len(files)} file  |  Upload file .txt baru ke chat")
            rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_main")])
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rows),
            )

    elif data == "connect_wa_info":
        checker_ok = is_checker_connected()
        if checker_ok:
            status_text = (
                f"✅ *WA Checker Terhubung*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 URL: `{WA_CHECKER_URL}`\n\n"
                f"Bot siap mengecek nomor WhatsApp\\!"
            )
        else:
            status_text = (
                "🔗 *Connect WA Checker*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Untuk cek status WA nomor, siapkan server checker sendiri\\.\n\n"
                "*Cara Setup:*\n"
                "1\\. Siapkan server WA checker \\(contoh: Baileys/WA\\-JS\\)\n"
                "2\\. Expose endpoint: `GET /check?phone=+628xxx`\n"
                "   Response JSON: `{\"registered\": true}`\n"
                "3\\. Tambah GitHub Secret:\n"
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
        loaded_from = None

        # Fallback: jika in-memory kosong (misal setelah restart), reload dari GitHub
        if not numbers:
            await query.edit_message_text("⏳ Memuat ulang file nomor dari storage...")
            # Coba load file yang terakhir digunakan user ini
            last_fname = _last_file_by_chat.get(chat_id)
            if last_fname:
                raw_lines = await asyncio.to_thread(load_file, last_fname)
                if raw_lines:
                    numbers = parse_numbers_from_text("\n".join(raw_lines))
                    if numbers:
                        _user_numbers[chat_id] = numbers
                        loaded_from = last_fname
            # Jika masih kosong, coba file terbaru dari seluruh storage
            if not numbers:
                files = await asyncio.to_thread(list_files)
                if files:
                    for f in files:  # files sudah diurut terbaru dulu
                        raw_lines = await asyncio.to_thread(load_file, f["filename"])
                        if raw_lines:
                            numbers = parse_numbers_from_text("\n".join(raw_lines))
                            if numbers:
                                _user_numbers[chat_id] = numbers
                                _last_file_by_chat[chat_id] = f["filename"]
                                loaded_from = f["filename"]
                                break

        if not numbers:
            await query.edit_message_text(
                "⚠️ Tidak ada daftar nomor.\n\nUpload file .txt atau pilih dari menu 📱 Cek Nomor WA.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📱 Pilih File", callback_data="num_info")],
                    [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
                ]),
            )
            return

        info_msg = f"🔄 Reload dari `{loaded_from}`..." if loaded_from else "🎲 Mengambil nomor acak baru..."
        await query.edit_message_text(info_msg, parse_mode="Markdown")
        chosen   = pick_random(numbers, 3)
        results  = await asyncio.to_thread(check_numbers, chosen)
        text_out = fmt_number_results(results, len(numbers))
        kb       = build_number_buttons(results, chat_id)
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("numfile_pick_"):
        filename = data[len("numfile_pick_"):]
        await query.edit_message_text(f"⏳ Memuat file `{filename}`...", parse_mode="Markdown")
        raw_lines = await asyncio.to_thread(load_file, filename)
        if not raw_lines:
            await query.edit_message_text(
                f"❌ File `{filename}` tidak ditemukan di storage.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Pilih File Lain", callback_data="num_info")
                ]]),
            )
            return
        numbers = parse_numbers_from_text("\n".join(raw_lines))
        if not numbers:
            await query.edit_message_text(
                f"❌ Tidak ada nomor valid di file `{filename}`.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Pilih File Lain", callback_data="num_info")
                ]]),
            )
            return
        _user_numbers[chat_id] = numbers
        _last_file_by_chat[chat_id] = filename   # ← track file terakhir user
        chosen   = pick_random(numbers, 3)
        await query.edit_message_text(f"🔍 Mengecek {len(chosen)} nomor dari {len(numbers)}...")
        results  = await asyncio.to_thread(check_numbers, chosen)
        text_out = fmt_number_results(results, len(numbers))
        kb       = build_number_buttons(results, chat_id)
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("numfile_del_"):
        filename = data[len("numfile_del_"):]
        # Minta konfirmasi
        await query.edit_message_text(
            f"🗑 *Hapus File?*\n━━━━━━━━━━━━━━━━━━━━\n`{filename}`\n\nYakin ingin menghapus?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Ya, Hapus",    callback_data=f"numfile_confirm_del_{filename}")],
                [InlineKeyboardButton("❌ Batal",        callback_data="num_info")],
            ]),
        )

    elif data.startswith("numfile_confirm_del_"):
        filename = data[len("numfile_confirm_del_"):]
        await query.edit_message_text(f"🗑 Menghapus `{filename}`...", parse_mode="Markdown")
        result = await asyncio.to_thread(delete_file, filename)
        if result["success"]:
            # Bersihkan cache in-memory jika user ini memakai file ini
            if _last_file_by_chat.get(chat_id) == filename:
                _last_file_by_chat.pop(chat_id, None)
                _user_numbers.pop(chat_id, None)
            # Refresh daftar file
            files = await asyncio.to_thread(list_files)
            if not files:
                await query.edit_message_text(
                    f"✅ File `{filename}` dihapus.\n\nBelum ada file lain tersimpan.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")
                    ]]),
                )
            else:
                rows = []
                lines = [
                    "✅ File dihapus.",
                    "",
                    "📁 *File Manager Nomor WA*",
                    "━━━━━━━━━━━━━━━━━━━━",
                ]
                for i, f in enumerate(files[:15], 1):
                    fname_  = f.get("filename", "?")
                    name_   = f.get("original_name", fname_)[:30]
                    total_  = f.get("total", "?")
                    region_ = f.get("region", "") or detect_region(name_)
                    date_s_ = f.get("uploaded_at", "")[:10]
                    region_tag_ = f"  {region_}" if region_ else ""
                    lines.append(f"{i}. `{name_}`{region_tag_}")
                    lines.append(f"   📊 {total_} nomor  📅 {date_s_}")
                    rows.append([
                        InlineKeyboardButton("✅ Cek Acak", callback_data=f"numfile_pick_{fname_}"),
                        InlineKeyboardButton("👁 Lihat",    callback_data=f"numfile_view_{fname_}"),
                        InlineKeyboardButton("🗑 Hapus",    callback_data=f"numfile_del_{fname_}"),
                    ])
                lines.append("━━━━━━━━━━━━━━━━━━━━")
                lines.append(f"Total: {len(files)} file")
                rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_main")])
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(rows),
                )
        else:
            await query.edit_message_text(
                f"❌ Gagal hapus: {result.get('error', 'Unknown')}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="num_info")
                ]]),
            )

    elif data.startswith("numfile_view_"):
        filename = data[len("numfile_view_"):]
        await query.edit_message_text(f"⏳ Memuat isi file `{filename}`...", parse_mode="Markdown")
        raw_lines = await asyncio.to_thread(load_file, filename)
        if not raw_lines:
            await query.edit_message_text(
                f"❌ File `{filename}` tidak ditemukan.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="num_info")
                ]]),
            )
            return
        all_numbers = parse_numbers_from_text("\n".join(raw_lines))
        preview     = all_numbers[:25]
        more        = len(all_numbers) - len(preview)
        lines = [
            f"👁 *Isi File:* `{filename}`",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 Total: *{len(all_numbers)}* nomor",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        for num in preview:
            lines.append(f"• `{num}`")
        if more > 0:
            lines.append(f"_...dan {more} nomor lainnya_")
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Cek Acak dari File Ini", callback_data=f"numfile_pick_{filename}")],
                [InlineKeyboardButton("🔙 Kembali ke Daftar",     callback_data="num_info")],
            ]),
        )

    elif data.startswith("copy_num_"):
        phone = data[len("copy_num_"):]
        await query.answer(f"Nomor: {phone}", show_alert=True)

    # ── Email Temp ────────────────────────────────────────────────────────────
    elif data == "gen":
        await query.edit_message_text("⏳ Generate email sementara...")
        result = await asyncio.to_thread(generator.generate_random)
        if result["success"]:
            context.user_data["last_temp_email"] = result["data"]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Cek Inbox", callback_data="check_inbox_temp")],
                [InlineKeyboardButton("🔄 Generate Lagi",  callback_data="gen")],
                [InlineKeyboardButton("🏠 Menu Utama",     callback_data="back_main")],
            ])
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]])
        await query.edit_message_text(
            fmt_tempmail(result), parse_mode="Markdown",
            reply_markup=kb,
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
        if result["success"]:
            context.user_data["last_temp_email"] = result["data"]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Cek Inbox", callback_data="check_inbox_temp")],
                [InlineKeyboardButton("🔄 Generate Lagi",  callback_data=f"prov_{provider}")],
                [InlineKeyboardButton("📋 Ganti Provider", callback_data="menu_provider")],
                [InlineKeyboardButton("🏠 Menu Utama",     callback_data="back_main")],
            ])
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]])
        await query.edit_message_text(
            fmt_tempmail(result), parse_mode="Markdown",
            reply_markup=kb,
        )

    elif data == "check_inbox_temp":
        last_email = context.user_data.get("last_temp_email")
        if not last_email:
            await query.edit_message_text(
                "❌ *Tidak ada email aktif.*\nSilakan generate email baru terlebih dahulu.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]]),
            )
            return

        provider = last_email.get("provider", "")
        email_addr = last_email.get("email", "")
        await query.edit_message_text(
            f"📥 *Mengecek Inbox...*\n📧 Email: `{email_addr}`\n🔖 Provider: *{provider}*",
            parse_mode="Markdown",
        )

        messages = await asyncio.to_thread(generator.check_inbox, last_email)
        if not messages:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh Inbox", callback_data="check_inbox_temp")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
            ])
            await query.edit_message_text(
                f"📭 *Inbox Kosong*\n\n📧 Email: `{email_addr}`\n\nBelum ada email masuk. Silakan kirim email ke alamat di atas lalu klik Refresh.",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

        # List messages (max 5)
        lines = [
            f"📥 *Inbox untuk:* `{email_addr}`",
            f"📊 Total: *{len(messages)}* email baru",
            "━━━━━━━━━━━━━━━━━━━━\n",
        ]
        rows = []
        for i, msg in enumerate(messages[:5], 1):
            sub = msg.get("subject", "No Subject")[:35]
            lines.append(f"✉️ *{i}. Dari:* `{msg.get('from')}`")
            lines.append(f"📌 *Sub:* _{sub}_")
            lines.append(f"📅 *Date:* {msg.get('date')}\n")

            rows.append([InlineKeyboardButton(f"📖 Baca Email #{i}", callback_data=f"read_temp_{msg.get('id')}")])

        rows.append([InlineKeyboardButton("🔄 Refresh Inbox", callback_data="check_inbox_temp")])
        rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")])
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("read_temp_"):
        msg_id = data[len("read_temp_"):]
        last_email = context.user_data.get("last_temp_email")
        if not last_email:
            await query.edit_message_text(
                "❌ Email tidak aktif.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")]]),
            )
            return

        await query.edit_message_text("⏳ Membaca email...")
        msg = await asyncio.to_thread(generator.read_message, last_email, msg_id)
        if not msg:
            await query.edit_message_text(
                "❌ Gagal memuat detail email.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Inbox", callback_data="check_inbox_temp")]]),
            )
            return

        body = msg.get("body", "")
        # Limit to 3000 chars to avoid Telegram message limits
        if len(body) > 3000:
            body = body[:3000] + "\n\n...[Teks Terpotong]..."

        lines = [
            f"📧 *Detail Email Masuk*",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"👤 *Dari:* `{msg.get('from')}`",
            f"📌 *Subject:* {msg.get('subject')}",
            f"📅 *Tanggal:* {msg.get('date')}",
            f"━━━━━━━━━━━━━━━━━━━━\n",
            f"{body}",
        ]
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Kembali ke Inbox", callback_data="check_inbox_temp")],
                [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
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
        ch_status  = "✅ Terhubung" if checker_ok else "⚠️ Belum setup"
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

    # ── Auto Generate SMTP ────────────────────────────────────────────────────
    elif data == "autogen_smtp":
        await query.edit_message_text(
            "🤖 *Auto Generate SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Bot akan otomatis membuat akun SMTP via backend — "
            "tanpa perlu input manual apapun.\n\n"
            "📌 *Pilih provider:*\n\n"
            "🌐 *Mail.tm* — gratis, tanpa token, langsung jadi\n"
            "📬 *Mailtrap* — butuh `MAILTRAP_API_TOKEN` di env\n\n"
            "Klik tombol di bawah untuk mulai generate:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Generate via Mail.tm",   callback_data="autogen_mailtm")],
                [InlineKeyboardButton("📬 Generate via Mailtrap",  callback_data="autogen_mailtrap")],
                [InlineKeyboardButton("🔙 Kembali",                callback_data="back_main")],
            ]),
        )

    elif data in ("autogen_mailtm", "autogen_mailtrap"):
        provider = "mailtm" if data == "autogen_mailtm" else "mailtrap"
        pname    = "Mail.tm" if provider == "mailtm" else "Mailtrap"
        await query.edit_message_text(
            f"⏳ *Auto-generate SMTP via {pname}...*\nMohon tunggu...",
            parse_mode="Markdown",
        )
        result = await asyncio.to_thread(auto_gen_smtp, provider)
        if not result["success"]:
            await query.edit_message_text(
                f"❌ *Generate Gagal*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ {result.get('error', 'Unknown error')}\n\n"
                f"💡 {'Pastikan `MAILTRAP_API_TOKEN` diset di env.' if provider == 'mailtrap' else 'Coba lagi atau pilih provider lain.'}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Coba Lagi",  callback_data=data)],
                    [InlineKeyboardButton("🔙 Kembali",    callback_data="autogen_smtp")],
                ]),
            )
            return
        save_result = await asyncio.to_thread(manager.add_auto_generated, result)
        if not save_result["success"]:
            await query.edit_message_text(
                f"❌ *Gagal simpan akun*\n{save_result.get('error', '')}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Kembali", callback_data="autogen_smtp")
                ]]),
            )
            return
        key = save_result.get("email", result.get("key", "-"))
        await query.edit_message_text(
            f"✅ *SMTP Auto-Generate Berhasil!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔖 Provider : `{result.get('provider', pname)}`\n"
            f"👤 Key/User : `{key}`\n"
            f"🔑 Password : `{result.get('password', '-')}`\n"
            f"📤 SMTP     : `{result.get('smtp_host')}:{result.get('smtp_port')}`\n"
            f"📥 IMAP     : `{result.get('imap_host')}:{result.get('imap_port')}`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 {result.get('note', '')}\n\n"
            f"💾 Tersimpan otomatis & siap digunakan!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 Generate Lagi",    callback_data=data)],
                [InlineKeyboardButton("📂 Lihat Akun SMTP", callback_data="list_smtp")],
                [InlineKeyboardButton("🏠 Menu Utama",       callback_data="back_main")],
            ]),
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
_STARTUP_NOTIF_FILE = Path(__file__).parent / ".last_startup_run_id"


def _should_send_startup_notif() -> bool:
    """Hanya kirim startup notif jika RUN_ID ini berbeda dari terakhir kali."""
    if RUN_ID == "local":
        return True  # lokal selalu kirim
    try:
        if _STARTUP_NOTIF_FILE.exists():
            last = _STARTUP_NOTIF_FILE.read_text().strip()
            if last == RUN_ID:
                return False  # sudah terkirim untuk run ini
        _STARTUP_NOTIF_FILE.write_text(RUN_ID)
        return True
    except Exception:
        return True


async def send_startup_notification(bot: Bot):
    if not ADMIN_CHAT:
        return
    if not _should_send_startup_notif():
        logger.info("Startup notif skip — sudah terkirim untuk run ini.")
        return
    checker_ok = is_checker_connected()
    ch_info    = f"✅ WA Checker: `{WA_CHECKER_URL}`" if checker_ok else "⚠️ WA Checker belum setup"
    mt_status  = "✅ MAILTRAP_API_TOKEN tersedia" if MAILTRAP_API_TOKEN else "⚠️ MAILTRAP_API_TOKEN belum diset"
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
                f"📬 {mt_status}\n"
                f"📧 Provider Temp: {len(generator.list_providers())}\n"
                f"📂 Akun SMTP: {manager.count()}\n"
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
    app.add_handler(CommandHandler("autogen",     cmd_autogen))
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
            BotCommand("autogen",     "🤖 Auto generate SMTP via backend"),
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
