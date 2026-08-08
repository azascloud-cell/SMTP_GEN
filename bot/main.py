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
    format_banding_templates,
    format_gacha_method,
    get_country_info,
    is_checker_connected,
    parse_numbers_from_text,
    pick_random,
    status_emoji,
    status_label,
    search_by_prefix,
)
from smtp_auto_generator import (
    MAILERSEND_API_KEY,
    auto_gen_smtp,
)
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
    mark_maybe_notified,
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
manager    = SMTPManager()

# ── Helpers ───────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_admin(chat_id: int) -> bool:
    if not ADMIN_CHAT:
        return True
    return str(chat_id) == str(ADMIN_CHAT)


# ── Testimonial Channel ───────────────────────────────────────────────────────

TESTI_CHANNEL = os.environ.get("TESTI_CHANNEL", "")

async def post_to_testimonial_channel(bot: Bot, text: str):
    if not TESTI_CHANNEL:
        return
    try:
        await bot.send_message(
            chat_id=TESTI_CHANNEL,
            text=text,
            parse_mode="Markdown",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Gagal post ke testimonial channel: {e}")


# ── Menu & Start ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_chat.first_name or "User"

    checker_label = "🔗 WA Checker: Terhubung ✅" if is_checker_connected(chat_id) else "🔗 Connect WA Checker"

    keyboard = [
        [InlineKeyboardButton("📧 Generate Email Sementara", callback_data="gen_email")],
        [InlineKeyboardButton("📂 Kelola SMTP", callback_data="smtp_menu")],
        [InlineKeyboardButton("📱 Cek Nomor WA / Gacha", callback_data="wa_menu")],
        [InlineKeyboardButton("🔧 Banding Ban WhatsApp", callback_data="fix_menu")],
        [InlineKeyboardButton("🤖 Auto Gen SMTP", callback_data="autogen_menu")],
        # iVasms Temp Numbers
        rows = []
        rows.append([InlineKeyboardButton("🌍 iVasms Temp Numbers (OTP)",       callback_data="ivasms_main")])
        rows.append([InlineKeyboardButton("📊 Status Bot", callback_data="status_info")])
        rows.append([InlineKeyboardButton(checker_label, callback_data="wa_connect")])
        rows.append([InlineKeyboardButton("❓ Bantuan", callback_data="help_info")])
        keyboard = rows

    await update.message.reply_text(
        f"👋 *Halo, {name}!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Bot Management Nomor & SMTP*\n"
        f"⏰ {now_utc()}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Pilih menu di bawah untuk memulai:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ── Generate Email ────────────────────────────────────────────────────────────

async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _generate_email(update)


async def _generate_email(update: Update, query=None):
    try:
        addr = generator.generate()
        provider = generator.get_provider_name()
        inbox_url = generator.get_inbox_url()
    except Exception as e:  # noqa: BLE001
        msg_text = f"❌ *Gagal generate email*\nDetail: {e}"
        if query:
            await query.edit_message_text(msg_text, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg_text, parse_mode="Markdown")
        return

    text = (
        f"📧 *Email Sementara Dibuat!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📮 *Alamat:* `{addr}`\n"
        f"🏢 *Provider:* {provider}\n"
        f"🔗 *Cek Inbox:* {inbox_url}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Email ini bersifat sementara (receive-only).\n"
        f"Gunakan untuk menerima kode OTP atau verifikasi."
    )
    keyboard = [[InlineKeyboardButton("🔄 Generate Ulang", callback_data="gen_email")]]
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ── SMTP Management ──────────────────────────────────────────────────────────

async def cmd_addsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "➕ *Tambah SMTP Manual*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addsmtp email|password|imap_server`\n\n"
            "Contoh:\n"
            "`/addsmtp user@gmail.com|apppassword|imap.gmail.com`\n\n"
            "Untuk Gmail, gunakan App Password (bukan password biasa).",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(args)
    parts = raw.split("|")
    if len(parts) < 3:
        await update.message.reply_text("❌ Format salah. Gunakan: `/addsmtp email|password|imap_server`", parse_mode="Markdown")
        return

    email, password, imap_server = parts[0].strip(), parts[1].strip(), parts[2].strip()

    ok = manager.add_account(email, password, imap_server, chat_id=update.effective_chat.id)
    if ok:
        await update.message.reply_text(
            f"✅ *SMTP Ditambahkan!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📧 Email: `{email}`\n"
            f"🖥 IMAP: `{imap_server}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ Gagal menambahkan SMTP. Mungkin email sudah ada.", parse_mode="Markdown")


async def cmd_listsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    accounts = manager.list_accounts(chat_id=chat_id)
    if not accounts:
        await update.message.reply_text("📂 Belum ada akun SMTP yang tersimpan.\nGunakan `/addsmtp` untuk menambahkan.", parse_mode="Markdown")
        return

    lines = ["📂 *Daftar Akun SMTP*\n━━━━━━━━━━━━━━━━━━━━"]
    for i, acc in enumerate(accounts, 1):
        lines.append(f"{i}\ufe0f⃣ 📧 `{acc['email']}`\n   🖥 `{acc['imap_server']}`")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total: {len(accounts)} akun")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delsmtp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🗑 Format: `/delsmtp email`", parse_mode="Markdown")
        return
    email = context.args[0].strip()
    ok = manager.delete_account(email, chat_id=update.effective_chat.id)
    if ok:
        await update.message.reply_text(f"✅ SMTP `{email}` dihapus.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ SMTP `{email}` tidak ditemukan.", parse_mode="Markdown")


# ── WhatsApp Number Check / Gacha ─────────────────────────────────────────────

async def cmd_wa_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _wa_menu(update)


async def _wa_menu(update: Update, query=None):
    chat_id = (query or update).effective_chat.id if hasattr((query or update), "effective_chat") else None
    if chat_id is None and query:
        chat_id = query.message.chat_id

    checker_ok = is_checker_connected(chat_id) if chat_id else False
    checker_status = "✅ WA Checker terhubung" if checker_ok else "⚠️ WA Checker belum diset"

    keyboard = [
        [InlineKeyboardButton("🎲 Gacha 3 Nomor Acak", callback_data="gacha")],
        [InlineKeyboardButton("🔍 Cari Nomor by Prefix", callback_data="search_prompt")],
        [InlineKeyboardButton("📤 Upload File .txt", callback_data="upload_info")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="start")],
    ]

    text = (
        f"📱 *Cek Nomor WA / Gacha*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{checker_status}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Pilih menu di bawah:"
    )

    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_gacha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _gacha(update)


async def _gacha(update: Update, query=None):
    chat_id = (query or update).effective_chat.id if hasattr((query or update), "effective_chat") else None
    if chat_id is None and query:
        chat_id = query.message.chat_id

    if not is_checker_connected(chat_id):
        text = (
            "⚠️ *WA Checker belum terhubung*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Hubungkan WA Checker terlebih dahulu dengan:\n"
            "`/pair +628xxxxxxxx`\n\n"
            "Setelah itu, Anda bisa gacha nomor."
        )
        if query:
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    files = list_files(chat_id)
    if not files:
        text = (
            "📂 *Belum ada file nomor*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Upload file .txt berisi nomor (satu per baris) untuk mulai gacha."
        )
        if query:
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    all_numbers = []
    for f in files:
        lines = load_file(f["filename"], chat_id)
        nums = parse_numbers_from_text("\n".join(lines))
        all_numbers.extend(nums)

    if not all_numbers:
        text = "❌ Tidak ada nomor valid di file Anda."
        if query:
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    picked = pick_random(all_numbers, 3)
    results = check_numbers(picked, chat_id=chat_id)

    lines = ["🎲 *Gacha Nomor WA*\n━━━━━━━━━━━━━━━━━━━━"]
    for r in results:
        emoji = status_emoji(r["registered"])
        label = status_label(r["registered"])
        lines.append(f"{emoji} `{r['phone']}` — {label}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🟢 = Fresh (belum WA) | 🔴 = Terdaftar (sudah WA)")

    keyboard = [[InlineKeyboardButton("🎲 Gacha Lagi", callback_data="gacha")]]
    if query:
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ── File Upload ───────────────────────────────────────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    doc = update.message.document

    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Hanya file .txt yang didukung.")
        return

    try:
        file_path = await doc.get_file()
        content = await file_path.download_as_bytearray()
        text = content.decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Gagal download file: {e}")
        return

    nums = parse_numbers_from_text(text)
    if not nums:
        await update.message.reply_text("❌ Tidak ada nomor valid di file.")
        return

    filename = save_file(text, chat_id)
    region = detect_region(nums[0]) if nums else "Unknown"

    await update.message.reply_text(
        f"✅ *File Diupload!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 File: `{doc.file_name}`\n"
        f"📊 Nomor valid: {len(nums)}\n"
        f"🌍 Region: {region}",
        parse_mode="Markdown",
    )


# ── WhatsApp Fix / Banding ─────────────────────────────────────────────────────

async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🔧 *Banding Ban WhatsApp*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/fix +628xxxxxxxx`\n\n"
            "Bot akan generate SMTP, kirim email banding ke WhatsApp Support,\n"
            "dan monitor balasan secara otomatis.",
            parse_mode="Markdown",
        )
        return

    phone = context.args[0].strip()
    if not phone.startswith("+"):
        phone = "+" + phone

    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("⏳ Mempersiapkan banding...")

    try:
        smtp_acc = manager.get_account(chat_id=chat_id)
        if not smtp_acc:
            await msg.edit_text(
                "❌ *Belum ada SMTP*\n"
                "Tambahkan SMTP dengan `/addsmtp` atau gunakan `/autogen`.",
                parse_mode="Markdown",
            )
            return

        await msg.edit_text("⏳ Mengirim email banding...")
        result = await asyncio.to_thread(send_appeal_email, smtp_acc, phone)

        if result.get("success"):
            add_pending(phone, smtp_acc["email"], chat_id, user_info=update.effective_user.full_name)
            await msg.edit_text(
                f"✅ *Email Banding Terkirim!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 Nomor: `{phone}`\n"
                f"📧 SMTP: `{smtp_acc['email']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔄 Bot akan monitor balasan dari WhatsApp Support.\n"
                f"Anda akan diberi tahu saat ada balasan.",
                parse_mode="Markdown",
            )
        else:
            await msg.edit_text(
                f"❌ *Gagal kirim email*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ {result.get('error', 'Unknown error')}",
                parse_mode="Markdown",
            )
    except Exception as e:  # noqa: BLE001
        await msg.edit_text(f"❌ *Error:* {e}", parse_mode="Markdown")


# ── Auto Gen SMTP ──────────────────────────────────────────────────────────────

async def cmd_autogen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("⏳ Auto generate SMTP...")

    try:
        result = await asyncio.to_thread(auto_gen_smtp, chat_id)
        if result.get("success"):
            smtp_data = result["smtp"]
            await msg.edit_text(
                f"🤖 *SMTP Berhasil Dibuat!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📧 Email: `{smtp_data['email']}`\n"
                f"🔑 Password: `{smtp_data['password']}`\n"
                f"🖥 IMAP: `{smtp_data.get('imap_server', 'N/A')}`\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"SMTP siap digunakan untuk `/fix`.",
                parse_mode="Markdown",
            )
        else:
            await msg.edit_text(
                f"❌ *Auto gen gagal*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ {result.get('error', 'Unknown error')}",
                parse_mode="Markdown",
            )
    except Exception as e:  # noqa: BLE001
        await msg.edit_text(f"❌ *Error:* {e}", parse_mode="Markdown")


# ── Status ─────────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    checker_ok = is_checker_connected(chat_id)
    ch_status  = f"✅ Terhubung (`{WA_CHECKER_URL}`)" if checker_ok else "⚠️ Belum setup"

    await update.message.reply_text(
        f"📊 *Status Bot*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Waktu: {now_utc()}\n"
        f"🆔 Run ID: `{RUN_ID}`\n"
        f"📦 Repo: `{REPO}`\n"
        f"📱 WA Checker: {ch_status}\n"
        f"📧 Provider Temp: {len(generator.list_providers())}\n"
        f"📂 Akun SMTP: {manager.count()}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Bot berjalan normal!",
        parse_mode="Markdown",
    )


# ── Help ───────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Bantuan*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📧 `/generate` — Email sementara\n"
        "➕ `/addsmtp email|password|imap` — Tambah SMTP\n"
        "📂 `/listsmtp` — Lihat SMTP\n"
        "🗑 `/delsmtp email` — Hapus SMTP\n"
        "🎲 Upload .txt + pilih Gacha\n"
        "🔗 `/pair +phone` — Tautkan WA Checker\n"
        "🔧 `/fix +phone` — Banding ban WA\n"
        "🤖 `/autogen` — Auto gen SMTP\n"
        "🌍 `/ivasms` — iVasms Temp Numbers\n"
        "⚙️ `/setivasms email|pass|url` — Set iVasms admin\n"
        "🔑 `/setcookie cookie_data` — Set iVasms cookie\n"
        "🔄 `/update` — Update bot dari GitHub\n"
        "━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )


# ── Donasi ─────────────────────────────────────────────────────────────────────

async def cmd_donasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💚 *Dukung Bot Ini!*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Jika bot ini membantu, dukung pengembangannya:\n\n"
        "💰 *Saweria:* [saweria.co/azascloud](https://saweria.co/azascloud)\n"
        "💰 *Trakteer:* [trakteer.id/azascloud](https://trakteer.id/azascloud)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Terima kasih! 🙏",
        parse_mode="Markdown",
    )


# ── Search ─────────────────────────────────────────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🔍 *Cari Nomor by Prefix*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/search 628`\n\n"
            "Bot akan mencari nomor di file Anda yang dimulai dengan prefix tersebut.",
            parse_mode="Markdown",
        )
        return

    prefix = context.args[0].strip()
    chat_id = update.effective_chat.id
    results = search_by_prefix(prefix, chat_id)

    if not results:
        await update.message.reply_text("❌ Tidak ada nomor ditemukan dengan prefix tersebut.", parse_mode="Markdown")
        return

    lines = [f"🔍 *Hasil Pencarian: `{prefix}`*\n━━━━━━━━━━━━━━━━━━━━"]
    for r in results[:20]:
        lines.append(f"📱 `{r['phone']}` ({r.get('filename', '?')})")
    if len(results) > 20:
        lines.append(f"... dan {len(results) - 20} lainnya")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── iVasms Commands ────────────────────────────────────────────────────────────

async def cmd_ivasms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    from ivasms import get_assignment, list_combos

    assignment = get_assignment(chat_id)
    combos = list_combos()

    if not combos:
        await update.message.reply_text(
            "🌍 *iVasms Temp Numbers*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ Belum ada combo nomor yang tersedia.\n"
            "Admin perlu menambahkan combo dengan `/addcombo`.",
            parse_mode="Markdown",
        )
        return

    lines = ["🌍 *iVasms Temp Numbers*\n━━━━━━━━━━━━━━━━━━━━"]
    if assignment:
        lines.append(f"📱 Nomor Anda: `{assignment['phone']}`")
        lines.append(f"🌍 Region: +{assignment['country_code']}")
        lines.append("")
        lines.append("OTP yang masuk akan diteruskan ke Anda otomatis.")
    else:
        lines.append("Anda belum memiliki nomor. Pilih negara di bawah:")
        lines.append("")

    rows = []
    for code, nums in sorted(combos.items()):
        flag = get_country_info(f"+{code}").get("flag", "🌍")
        btn_text = f"{flag} +{code} ({len(nums)} nomor)"
        rows.append([InlineKeyboardButton(btn_text, callback_data=f"ivasms_get_{code}")])

    if assignment:
        rows.append([InlineKeyboardButton("❌ Batalkan / Lepas Nomor", callback_data="ivasms_release")])
    rows.append([InlineKeyboardButton("🔐 Admin Panel iVasms", callback_data="ivasms_admin")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cmd_addcombo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("❌ Hanya admin yang bisa menambah combo.", parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "➕ *Tambah Combo Nomor*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addcombo country_code|+628xxx,+628yyy,...`\n\n"
            "Contoh: `/addcombo 62|+62812345678,+62812345679`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    parts = raw.split("|", 1)
    if len(parts) < 2:
        await update.message.reply_text("❌ Format salah. Gunakan: `/addcombo country_code|nomor1,nomor2,...`", parse_mode="Markdown")
        return

    country_code = parts[0].strip()
    numbers = [n.strip() for n in parts[1].split(",")]

    from ivasms import add_combo
    ok = add_combo(country_code, numbers)
    if ok:
        await update.message.reply_text(
            f"✅ *Combo Ditambahkan!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 Country: +{country_code}\n"
            f"📊 Jumlah: {len(numbers)} nomor",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("❌ Gagal menambahkan combo. Pastikan format nomor valid.", parse_mode="Markdown")


async def cmd_setivasms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("❌ Hanya admin.", parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "⚙️ *Set Kredensial iVasms*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/setivasms email|password|base_url`\n\n"
            "Contoh:\n"
            "`/setivasms admin@example.com|securepass|https://ivas.tempnum.qzz.io`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    parts = raw.split("|")
    if len(parts) < 2:
        await update.message.reply_text("❌ Format salah. Gunakan: `/setivasms email|password|base_url`", parse_mode="Markdown")
        return

    email = parts[0].strip()
    password = parts[1].strip()
    base_url = parts[2].strip() if len(parts) > 2 else None

    from ivasms import update_credentials
    update_credentials(email, password, base_url)

    await update.message.reply_text(
        f"✅ *Kredensial iVasms Disimpan!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Email: `{email}`\n"
        f"🌐 Base URL: `{base_url or '(default)'}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Bot akan mencoba masuk ke dashboard pada permintaan OTP berikutnya.",
        parse_mode="Markdown",
    )


async def cmd_setcookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text("❌ Hanya admin.", parse_mode="Markdown")
        return

    if not context.args:
        await update.message.reply_text(
            "🔑 *Set Cookie Session iVasms*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/setcookie cookie_data`\n\n"
            "Cookie data bisa berupa:\n"
            "• Raw cookie header (XSRF-TOKEN=...; ivasms_session=...)\n"
            "• JSON array dari Chrome extension export\n\n"
            "Contoh:\n"
            "`/setcookie XSRF-TOKEN=...; ivas_sms_session=...`\n\n"
            "`/setcookie [{\"name\": \"ivas_sms_session\", \"value\": \"...\"}]`",
            parse_mode="Markdown",
        )
        return

    cookie_str = " ".join(context.args)

    from ivasms import update_cookies, check_ivasms_connection
    cookies = update_cookies(cookie_str)

    if not cookies:
        await update.message.reply_text("❌ Gagal parse cookie. Pastikan format cookie valid.", parse_mode="Markdown")
        return

    ok, status_msg = await asyncio.to_thread(check_ivasms_connection)

    from ivasms import get_credentials
    creds = get_credentials()

    keyboard = []
    if not ok:
        keyboard = [[InlineKeyboardButton("🔐 Admin Panel", callback_data="ivasms_admin")]]

    await update.message.reply_text(
        f"🔐 *Admin Panel iVasms & 2-Step Login*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *PROSES LOGIN 2 TAHAP (2x Login):*\n"
        f"  Step 1: Set Kredensial (Email, Password, Base URL)\n"
        f"  Gunakan command: `/setivasms email|password|base_url`\n"
        f"  Step 2: Set Cookie Session (Membypass Cloudflare/Proteksi)\n"
        f"  Gunakan command: `/setcookie cookie_data`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Email: `{creds.get('email', '-')}`\n"
        f"🌐 Base URL: `{creds.get('base_url', '-')}`\n"
        f"🔑 Cookie Session: {'Ada ✅' if cookies else 'Tidak Ada ❌'} \n"
        f"🔌 Status Koneksi: {'🟢 ' + status_msg if ok else '🔴 Terputus (' + status_msg + ')'}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Silakan gunakan tombol di bawah untuk mengelola koneksi:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


# ── WA Pairing ─────────────────────────────────────────────────────────────────

def _fetch_pairing_code(phone: str, chat_id: int) -> dict:
    import json
    import urllib.parse
    import urllib.request
    encoded_phone = urllib.parse.quote(phone)
    url = f"{WA_CHECKER_URL}/pair?phone={encoded_phone}&chat_id={chat_id}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


async def cmd_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tautkan WA Checker via Pairing Code. Format: /pair +phone"""
    phone = " ".join(context.args).strip() if context.args else ""
    if not phone:
        await update.message.reply_text(
            "🔗 *WhatsApp Pairing*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/pair +628xxxxxxxx`\n\n"
            "Silakan masukkan nomor telepon Anda beserta kode negara untuk meminta pairing code dari WA Checker.",
            parse_mode="Markdown",
        )
        return

    if not phone.startswith("+"):
        phone = "+" + phone

    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("⏳ Meminta pairing code dari WA Checker...")

    try:
        resp_data = await asyncio.to_thread(_fetch_pairing_code, phone, chat_id)

        if resp_data.get("success"):
            code = resp_data.get("code")
            clean_phone = resp_data.get("phone")
            await msg.edit_text(
                f"🔗 *WhatsApp Pairing Code:*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 Code  : `{code}`\n"
                f"📱 Nomor : `{clean_phone}`\n\n"
                f"Silakan buka WhatsApp Anda -> perangkat tertaut -> tautkan perangkat -> tautkan dengan nomor telepon, lalu masukkan kode di atas.",
                parse_mode="Markdown",
            )
        else:
            error_msg = resp_data.get("error", "Gagal meminta pairing code.")
            await msg.edit_text(f"❌ *Gagal:* {error_msg}", parse_mode="Markdown")
    except Exception as e:  # noqa: BLE001
        await msg.edit_text(f"❌ *Error:* Gagal menghubungi WA Checker. Pastikan WA Checker terhubung.\nDetail: {e}", parse_mode="Markdown")


# ── IMAP Monitor Loop ──────────────────────────────────────────────────────────

async def imap_monitor_loop(bot: Bot):
    logger.info("IMAP monitor loop dimulai...")
    while True:
        try:
            pending = get_all_pending()
            for key, item in list(pending.items()):
                if item.get("notified"):
                    continue

                smtp_full = manager.get_account(item["smtp_email"], chat_id=item.get("chat_id"))
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
                    confirmed = reply.get("confirmed", True)

                    if not confirmed and item.get("maybe_notified"):
                        logger.debug(f"Skip maybe-notif untuk {key} — sudah pernah dikirim")
                        continue

                    import html as _html  # noqa: PLC0415
                    def _e(s: str) -> str:
                        return _html.escape(str(s or "-"))

                    from_safe    = _e(reply.get("from",    "-"))
                    subject_safe = _e(reply.get("subject", "-"))
                    date_safe    = _e(reply.get("date",    "-"))
                    body_safe    = _e(reply.get("body",    "")[:500])
                    phone_safe   = _e(phone)
                    smtp_safe    = _e(smtp_em)

                    if confirmed:
                        header = "📬 <b>EMAIL DIBALAS OLEH WHATSAPP!</b>"
                        info   = (
                            "👋 Kabar gembira! Email banding kamu sudah dibalas oleh WhatsApp Support.\n\n"
                            "Kemungkinan besar nomor kamu sudah berhasil diaktifkan kembali.\n"
                            "Coba buka WhatsApp dan cek nomornya ya.\n"
                            "Kalau masih ada kendala, coba banding ulang dengan /fix."
                        )

                        u_info = item.get("user_info", "User")
                        testi_reply_msg = (
                            f"🎉 *EMAIL BANDING DIBALAS! (SUKSES)* 🎉\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📱 *Nomor:* `{phone}`\n"
                            f"👤 *User:* {u_info}\n"
                            f"📧 *SMTP:* `{smtp_em}`\n"
                            f"📩 *Subject:* {reply.get('subject', '-')}\n"
                            f"✅ *Keterangan:* Akun kemungkinan besar telah diaktifkan kembali oleh WhatsApp!\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        )
                        asyncio.create_task(post_to_testimonial_channel(bot, testi_reply_msg))
                    else:
                        header = "📬 <b>ADA EMAIL MASUK DI INBOX!</b>"
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
                                f"📱 Nomor : <code>{phone_safe}</code>\n"
                                f"📧 SMTP  : <code>{smtp_safe}</code>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"{info}\n\n"
                                f"📩 <b>DETAIL EMAIL</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📧 Dari    : {from_safe}\n"
                                f"📌 Subject : {subject_safe}\n"
                                f"📅 Tanggal : {date_safe}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"<pre>{body_safe}</pre>"
                            ),
                            parse_mode="HTML",
                        )
                        if confirmed:
                            mark_notified(key)
                        else:
                            mark_maybe_notified(key)
                        logger.info(f"Notifikasi balasan terkirim ke {chat_id} untuk {phone} (confirmed={confirmed})")
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Gagal kirim notif ke {chat_id}: {e}")

                elif check_count > 0 and check_count % 20 == 0:
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
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Gagal kirim status update ke {chat_id}: {e}")

        except Exception as e:  # noqa: BLE001
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
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Gagal notif auto-update: {e}")
                elif not trig["success"]:
                    logger.warning(f"Auto-update trigger gagal: {trig.get('error')}")

        except Exception as e:  # noqa: BLE001
            logger.error(f"Auto-update loop error: {e}")

        await asyncio.sleep(1800)


async def ivasms_poll_loop(bot: Bot):
    logger.info("iVasms polling background loop dimulai...")
    from ivasms import fetch_ivasms_messages, get_user_by_number, log_otp, detect_service, extract_otp

    processed_msg_ids = set()

    while True:
        try:
            messages = await asyncio.to_thread(fetch_ivasms_messages)
            for msg in messages:
                msg_id = msg["id"]
                if msg_id in processed_msg_ids:
                    continue
                processed_msg_ids.add(msg_id)

                phone = msg["phone"]
                text  = msg["text"]
                otp   = extract_otp(text)
                svc   = detect_service(text)

                chat_id = get_user_by_number(phone)
                if chat_id:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"📩 *OTP Diterima!*\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📱 Nomor: `{phone}`\n"
                                f"🔑 OTP: `{otp}`\n"
                                f"💬 Layanan: {svc}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"📝 Pesan:\n{text[:200]}"
                            ),
                            parse_mode="Markdown",
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Gagal kirim OTP notif ke {chat_id}: {e}")

                    log_otp(phone, otp, text, chat_id=chat_id)

                if len(processed_msg_ids) > 500:
                    processed_msg_ids = set(list(processed_msg_ids)[-200:])

        except Exception as e:  # noqa: BLE001
            logger.error(f"Error in ivasms_poll_loop: {e}")

        await asyncio.sleep(30)


# ── Button Handler ─────────────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "start":
        chat_id = query.message.chat_id
        name = query.from_user.first_name or "User"

        checker_label = "🔗 WA Checker: Terhubung ✅" if is_checker_connected(chat_id) else "🔗 Connect WA Checker"

        keyboard = [
            [InlineKeyboardButton("📧 Generate Email Sementara", callback_data="gen_email")],
            [InlineKeyboardButton("📂 Kelola SMTP", callback_data="smtp_menu")],
            [InlineKeyboardButton("📱 Cek Nomor WA / Gacha", callback_data="wa_menu")],
            [InlineKeyboardButton("🔧 Banding Ban WhatsApp", callback_data="fix_menu")],
            [InlineKeyboardButton("🤖 Auto Gen SMTP", callback_data="autogen_menu")],
            [InlineKeyboardButton("🌍 iVasms Temp Numbers (OTP)", callback_data="ivasms_main")],
            [InlineKeyboardButton("📊 Status Bot", callback_data="status_info")],
            [InlineKeyboardButton(checker_label, callback_data="wa_connect")],
            [InlineKeyboardButton("❓ Bantuan", callback_data="help_info")],
        ]

        await query.edit_message_text(
            f"👋 *Halo, {name}!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 *Bot Management Nomor & SMTP*\n"
            f"⏰ {now_utc()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Pilih menu di bawah untuk memulai:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    elif data == "gen_email":
        await _generate_email(update=None, query=query)

    elif data == "smtp_menu":
        keyboard = [
            [InlineKeyboardButton("➕ Tambah SMTP", callback_data="smtp_add_info")],
            [InlineKeyboardButton("📂 List SMTP", callback_data="smtp_list")],
            [InlineKeyboardButton("🗑 Hapus SMTP", callback_data="smtp_del_info")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="start")],
        ]
        await query.edit_message_text(
            "📂 *Kelola SMTP*\n━━━━━━━━━━━━━━━━━━━━\nPilih menu di bawah:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "smtp_add_info":
        await query.edit_message_text(
            "➕ *Tambah SMTP Manual*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addsmtp email|password|imap_server`\n\n"
            "Contoh:\n"
            "`/addsmtp user@gmail.com|apppassword|imap.gmail.com`\n\n"
            "Untuk Gmail, gunakan App Password (bukan password biasa).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="smtp_menu")]]),
        )

    elif data == "smtp_list":
        chat_id = query.message.chat_id
        accounts = manager.list_accounts(chat_id=chat_id)
        if not accounts:
            text = "📂 Belum ada akun SMTP yang tersimpan."
        else:
            lines = ["📂 *Daftar Akun SMTP*\n━━━━━━━━━━━━━━━━━━━━"]
            for i, acc in enumerate(accounts, 1):
                lines.append(f"{i}\ufe0f⃣ 📧 `{acc['email']}`\n   🖥 `{acc['imap_server']}`")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"Total: {len(accounts)} akun")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="smtp_menu")]]))

    elif data == "smtp_del_info":
        await query.edit_message_text(
            "🗑 *Hapus SMTP*\n━━━━━━━━━━━━━━━━━━━━\nFormat: `/delsmtp email`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="smtp_menu")]]),
        )

    elif data == "wa_menu":
        await _wa_menu(update=None, query=query)

    elif data == "gacha":
        await _gacha(update=None, query=query)

    elif data == "upload_info":
        await query.edit_message_text(
            "📤 *Upload File .txt*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim file .txt berisi nomor telepon (satu nomor per baris) ke bot.\n"
            "Setelah upload, gunakan Gacha untuk pick nomor acak.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="wa_menu")]]),
        )

    elif data == "search_prompt":
        await query.edit_message_text(
            "🔍 *Cari Nomor by Prefix*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/search prefix`\n\n"
            "Contoh: `/search 628`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="wa_menu")]]),
        )

    elif data == "fix_menu":
        await query.edit_message_text(
            "🔧 *Banding Ban WhatsApp*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/fix +628xxxxxxxx`\n\n"
            "Bot akan generate SMTP, kirim email banding ke WhatsApp Support,\n"
            "dan monitor balasan secara otomatis.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
        )

    elif data == "autogen_menu":
        await query.edit_message_text(
            "🤖 *Auto Gen SMTP*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik `/autogen` untuk membuat SMTP otomatis via backend.\n"
            "SMTP siap digunakan untuk `/fix`.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
        )

    elif data == "status_info":
        chat_id = query.message.chat_id
        checker_ok = is_checker_connected(chat_id)
        ch_status  = "✅ Terhubung" if checker_ok else "⚠️ Belum setup"

        await query.edit_message_text(
            f"📊 *Status Bot*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ Waktu: {now_utc()}\n"
            f"🆔 Run ID: `{RUN_ID}`\n"
            f"📦 Repo: `{REPO}`\n"
            f"📱 WA Checker: {ch_status}\n"
            f"📧 Provider Temp: {len(generator.list_providers())}\n"
            f"📂 Akun SMTP: {manager.count()}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Bot berjalan normal!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
        )

    elif data == "wa_connect":
        chat_id = query.message.chat_id
        checker_ok = is_checker_connected(chat_id)

        if checker_ok:
            await query.edit_message_text(
                f"🔗 *WA Checker Status*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ WA Checker terhubung!\n"
                f"🌐 URL: `{WA_CHECKER_URL}`\n\n"
                f"Anda sudah bisa gacha nomor dan cek status WA.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
            )
        else:
            await query.edit_message_text(
                f"🔗 *Tautkan WA Checker*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ WA Checker belum terhubung.\n\n"
                f"Gunakan command:\n"
                f"`/pair +628xxxxxxxx`\n\n"
                f"Untuk menautkan WhatsApp Anda dengan WA Checker via pairing code.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
            )

    elif data == "help_info":
        await query.edit_message_text(
            "❓ *Bantuan*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📧 `/generate` — Email sementara\n"
            "➕ `/addsmtp email|password|imap` — Tambah SMTP\n"
            "📂 `/listsmtp` — Lihat SMTP\n"
            "🗑 `/delsmtp email` — Hapus SMTP\n"
            "🎲 Upload .txt + pilih Gacha\n"
            "🔗 `/pair +phone` — Tautkan WA Checker\n"
            "🔧 `/fix +phone` — Banding ban WA\n"
            "🤖 `/autogen` — Auto gen SMTP\n"
            "🌍 `/ivasms` — iVasms Temp Numbers\n"
            "⚙️ `/setivasms email|pass|url` — Set iVasms admin\n"
            "🔑 `/setcookie cookie_data` — Set iVasms cookie\n"
            "🔄 `/update` — Update bot dari GitHub\n"
            "━━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
        )

    elif data == "ivasms_main":
        chat_id = query.message.chat_id
        from ivasms import get_assignment, list_combos

        assignment = get_assignment(chat_id)
        combos = list_combos()

        if not combos:
            await query.edit_message_text(
                "🌍 *iVasms Temp Numbers*\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ Belum ada combo nomor yang tersedia.\n"
                "Admin perlu menambahkan combo dengan `/addcombo`.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start")]]),
            )
            return

        lines = ["🌍 *iVasms Temp Numbers*\n━━━━━━━━━━━━━━━━━━━━"]
        if assignment:
            lines.append(f"📱 Nomor Anda: `{assignment['phone']}`")
            lines.append(f"🌍 Region: +{assignment['country_code']}")
            lines.append("")
            lines.append("OTP yang masuk akan diteruskan ke Anda otomatis.")
        else:
            lines.append("Anda belum memiliki nomor. Pilih negara di bawah:")
            lines.append("")

        rows = []
        for code, nums in sorted(combos.items()):
            flag = get_country_info(f"+{code}").get("flag", "🌍")
            btn_text = f"{flag} +{code} ({len(nums)} nomor)"
            rows.append([InlineKeyboardButton(btn_text, callback_data=f"ivasms_get_{code}")])

        if assignment:
            rows.append([InlineKeyboardButton("❌ Batalkan / Lepas Nomor", callback_data="ivasms_release")])
        rows.append([InlineKeyboardButton("🔐 Admin Panel iVasms", callback_data="ivasms_admin")])

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data == "ivasms_release":
        chat_id = query.message.chat_id
        from ivasms import release_number
        release_number(chat_id)
        query.data = "ivasms_main"
        await button_handler(update, context)

    elif data.startswith("ivasms_get_"):
        code = data[len("ivasms_get_"):]
        chat_id = query.message.chat_id
        from ivasms import assign_number

        result = assign_number(chat_id, code)
        if result:
            await query.edit_message_text(
                f"✅ *Nomor Dialokasikan!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 Nomor: `{result['phone']}`\n"
                f"🌍 Region: +{result['country_code']}\n\n"
                f"OTP yang masuk akan diteruskan ke Anda otomatis.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Batalkan / Lepas Nomor", callback_data="ivasms_release")],
                    [InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_main")],
                ]),
            )
        else:
            await query.edit_message_text(
                "❌ Tidak ada nomor tersedia untuk region ini.\n"
                "Coba region lain atau minta admin tambah combo.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_main")],
                ]),
            )

    elif data == "ivasms_admin":
        if not is_admin(query.message.chat_id):
            await query.edit_message_text("❌ Hanya admin.", parse_mode="Markdown")
            return

        from ivasms import get_credentials, load_ivasms_data, check_ivasms_connection
        creds = get_credentials()
        ivasms_data = load_ivasms_data()
        has_cookies = "Ada ✅" if ivasms_data.get("cookies") else "Tidak Ada ❌"

        ok, status_msg = await asyncio.to_thread(check_ivasms_connection)

        await query.edit_message_text(
            f"🔐 *Admin Panel iVasms & 2-Step Login*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *PROSES LOGIN 2 TAHAP (2x Login):*\n"
            f"  Step 1: Set Kredensial (Email, Password, Base URL)\n"
            f"  Gunakan command: `/setivasms email|password|base_url`\n"
            f"  Step 2: Set Cookie Session (Membypass Cloudflare/Proteksi)\n"
            f"  Gunakan command: `/setcookie cookie_data`\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📧 Email: `{creds.get('email', '-')}`\n"
            f"🌐 Base URL: `{creds.get('base_url', '-')}`\n"
            f"🔑 Cookie Session: {has_cookies} \n"
            f"🔌 Status Koneksi: {'🟢 ' + status_msg if ok else '🔴 Terputus (' + status_msg + ')'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Silakan gunakan tombol di bawah untuk mengelola koneksi:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔌 Cek Status Koneksi", callback_data="ivasms_check_conn")],
                [InlineKeyboardButton("⚙️ Set Kredensial (Step 1)", callback_data="ivasms_creds_prompt")],
                [InlineKeyboardButton("🔑 Set Cookie Session (Step 2)", callback_data="ivasms_setcookie_prompt")],
                [InlineKeyboardButton("🗑️ Hapus Cookie Session", callback_data="ivasms_delcookie")],
                [InlineKeyboardButton("📥 Tambah Combo Nomor", callback_data="ivasms_addcombo_prompt")],
                [InlineKeyboardButton("🗑️ Hapus Combo Negara", callback_data="ivasms_delcombo_prompt")],
                [InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_main")],
            ]),
        )

    elif data == "ivasms_check_conn":
        from ivasms import check_ivasms_connection
        ok, status_msg = await asyncio.to_thread(check_ivasms_connection)
        status_text = f"🟢 {status_msg}" if ok else f"🔴 {status_msg}"
        await query.edit_message_text(
            f"🔌 *Cek Status Koneksi iVasms*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_admin")]]),
        )
        query.data = "ivasms_admin"

    elif data == "ivasms_setcookie_prompt":
        await query.edit_message_text(
            "🔑 *Set Cookie Session iVasms*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/setcookie cookie_data`\n\n"
            "Cookie data bisa berupa:\n"
            "• Raw cookie header (XSRF-TOKEN=...; ivas_sms_session=...)\n"
            "• JSON array dari Chrome extension export\n\n"
            "Contoh:\n"
            "`/setcookie XSRF-TOKEN=...; ivas_sms_session=...`\n\n"
            "`/setcookie [{\"name\": \"ivas_sms_session\", \"value\": \"...\"}]`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal", callback_data="ivasms_admin")]]),
        )

    elif data == "ivasms_delcookie":
        from ivasms import load_ivasms_data, save_ivasms_data
        ivasms_data = load_ivasms_data()
        if "cookies" in ivasms_data:
            del ivasms_data["cookies"]
            save_ivasms_data(ivasms_data)
            await query.edit_message_text(
                "✅ Cookie session iVasms dihapus.\n"
                "Gunakan `/setcookie` untuk set cookie baru.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_admin")]]),
            )
        else:
            await query.edit_message_text(
                "ℹ️ Tidak ada cookie yang tersimpan.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_admin")]]),
            )
        query.data = "ivasms_admin"

    elif data == "ivasms_addcombo_prompt":
        await query.edit_message_text(
            "➕ *Tambah Combo Nomor*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/addcombo country_code|+628xxx,+628yyy,...`\n\n"
            "Contoh: `/addcombo 62|+62812345678,+62812345679`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal", callback_data="ivasms_admin")]]),
        )

    elif data == "ivasms_delcombo_prompt":
        from ivasms import list_combos
        combos = list_combos()
        if not combos:
            await query.edit_message_text(
                "ℹ️ Belum ada combo yang tersimpan.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_admin")]]),
            )
            return

        rows = []
        for code, nums in sorted(combos.items()):
            flag = get_country_info(f"+{code}").get("flag", "🌍")
            btn_text = f"🗑 {flag} +{code} ({len(nums)} nomor)"
            rows.append([InlineKeyboardButton(btn_text, callback_data=f"ivasms_del_{code}")])
        rows.append([InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_admin")])

        await query.edit_message_text(
            "🗑 *Hapus Combo Negara*\n━━━━━━━━━━━━━━━━━━━━\nPilih combo yang ingin dihapus:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("ivasms_del_"):
        code = data[len("ivasms_del_"):]
        from ivasms import delete_combo
        delete_combo(code)
        await query.edit_message_text(
            f"✅ Combo +{code} dihapus.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="ivasms_delcombo_prompt")]]),
        )
        query.data = "ivasms_delcombo_prompt"

    elif data == "ivasms_creds_prompt":
        await query.edit_message_text(
            "⚙️ *Set Kredensial iVasms*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Format: `/setivasms email|password|base_url`\n\n"
            "Contoh:\n"
            "`/setivasms admin@example.com|securepass|https://ivas.tempnum.qzz.io`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Batal", callback_data="ivasms_admin")]]),
        )


# ── Startup Notification ──────────────────────────────────────────────────────

_STARTUP_NOTIF_FILE = Path(".startup_notif")

def _should_send_startup_notif() -> bool:
    try:
        if _STARTUP_NOTIF_FILE.exists():
            last = _STARTUP_NOTIF_FILE.read_text().strip()
            if last == RUN_ID:
                return False
        _STARTUP_NOTIF_FILE.write_text(RUN_ID)
        return True
    except Exception:  # noqa: BLE001
        return True


async def send_startup_notification(bot: Bot):
    if not ADMIN_CHAT:
        return
    if not _should_send_startup_notif():
        logger.info("Startup notif skip — sudah terkirim untuk run ini.")
        return
    checker_ok = is_checker_connected()
    ch_info    = f"✅ WA Checker: `{WA_CHECKER_URL}`" if checker_ok else "⚠️ WA Checker belum setup"
    ms_status  = "✅ MAILERSEND_API_KEY tersedia" if MAILERSEND_API_KEY else "⚠️ MAILERSEND_API_KEY belum diset"
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
                f"📬 {ms_status}\n"
                f"📧 Provider Temp: {len(generator.list_providers())}\n"
                f"📂 Akun SMTP: {manager.count()}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ Bot siap menerima perintah!"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Notif startup gagal: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("generate",    cmd_generate))
    app.add_handler(CommandHandler("addsmtp",     cmd_addsmtp))
    app.add_handler(CommandHandler("listsmtp",    cmd_listsmtp))
    app.add_handler(CommandHandler("delsmtp",     cmd_delsmtp))
    app.add_handler(CommandHandler("status",      cmd_status))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("fix",         cmd_fix))
    app.add_handler(CommandHandler("donasi",      cmd_donasi))
    app.add_handler(CommandHandler("autogen",     cmd_autogen))
    app.add_handler(CommandHandler("update",      cmd_update))
    app.add_handler(CommandHandler("pair",        cmd_pair))
    app.add_handler(CommandHandler("search",      cmd_search))
    app.add_handler(CommandHandler("ivasms",      cmd_ivasms))
    app.add_handler(CommandHandler("addcombo",    cmd_addcombo))
    app.add_handler(CommandHandler("setivasms",   cmd_setivasms))
    app.add_handler(CommandHandler("setcookie",   cmd_setcookie))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_document))

    async def post_init(application: Application):
        await send_startup_notification(application.bot)
        await application.bot.set_my_commands([
            BotCommand("start",       "Menu utama"),
            BotCommand("generate",    "📧 Email sementara (receive only)"),
            BotCommand("addsmtp",     "➕ Tambah SMTP manual (Gmail/Yahoo)"),
            BotCommand("listsmtp",    "📂 Lihat semua akun SMTP"),
            BotCommand("delsmtp",     "🗑 Hapus akun SMTP"),
            BotCommand("fix",         "🔧 Banding ban WhatsApp"),
            BotCommand("autogen",     "🤖 Auto generate SMTP via backend"),
            BotCommand("pair",        "🔗 Tautkan WhatsApp Checker via Pairing Code"),
            BotCommand("search",      "🔍 Cari nomor berdasarkan prefix"),
            BotCommand("ivasms",      "🌍 iVasms Temp Numbers & OTP"),
            BotCommand("setivasms",   "⚙️ Set kredensial admin iVasms"),
            BotCommand("setcookie",   "🔑 Set cookie session admin iVasms"),
            BotCommand("update",      "🔄 Cek & update bot dari GitHub"),
            BotCommand("status",      "📊 Status bot"),
            BotCommand("help",        "❓ Bantuan"),
        ])
        asyncio.create_task(imap_monitor_loop(application.bot))
        asyncio.create_task(auto_update_loop(application.bot))
        asyncio.create_task(ivasms_poll_loop(application.bot))

    app.post_init = post_init
    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
