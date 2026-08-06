import asyncio
import io
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import openpyxl
from typing import Optional, Tuple, List, Dict

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
    _normalize,
    check_wa_registered,
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

# ── Logging ───────────────────────────────────────────────────────────��[...]
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────[...]
BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
RUN_ID     = os.environ.get("GITHUB_RUN_ID", "local")

def is_user_admin(chat_id) -> bool:
    chat_id_str = str(chat_id).strip()
    if not chat_id_str:
        return False
    if ADMIN_CHAT and chat_id_str == str(ADMIN_CHAT).strip():
        return True
    admin_ids = [aid.strip() for aid in os.environ.get("ADMIN_IDS", "").split(",") if aid.strip()]
    return chat_id_str in admin_ids
REPO       = os.environ.get("GITHUB_REPOSITORY", "SMTP_GEN")

generator = SMTPGenerator()
manager   = SMTPManager()

BANDING_TEMPLATES_RAW = {
    "id": "Halo Tim Dukungan WhatsApp, Nomor saya ({phone}) terblokir secara tidak sengaja. Saya selalu mematuhi Ketentuan Layanan WhatsApp dan tidak pernah mengirim spam. Nomor ini sangat penting[...]",
    "en": "Hello WhatsApp Support Team, My phone number ({phone}) was banned by mistake. I strictly follow WhatsApp Terms of Service and have not sent any spam or violation content. This is my pri[...]",
    "ar": "مرحبًا فريق دعم WhatsApp، تم حظر رقم هاتفي ({phone}) عن طريق الخطأ. أنا ألتزم تمامًا بشروط خدمة WhatsApp ولم أقم بإ�[...]",
    "ru": "Здравствуйте, служба поддержки WhatsApp! Мой номер телефона ({phone}) был заблокирован по ошибке. Я строго со�[...]",
    "fr": "Bonjour l'équipe Support WhatsApp, Mon numéro de téléphone ({phone}) a été bloqué par erreur. Je respecte strictement les Conditions d'utilisation de WhatsApp et n'ai envoyé auc[...]",
}


# ── Simpan daftar nomor per user sementara (in-memory) ───────────────────────
# { chat_id: ["+628...", ...] }
_user_numbers: Dict[int, List[str]] = {}

# ── Simpan nama file terakhir yang digunakan per user ─────────────────────────
# Dipakai oleh num_reroll agar bisa reload dari GitHub setelah restart
# { chat_id: "filename.txt" }
_last_file_by_chat: Dict[int, str] = {}

# ── Index terakhir SMTP yang digunakan untuk rotasi round-robin ───────────────
_last_smtp_index: int = 0


# ── Helpers ───────────────────────────────────────────────────────────�[...]
import random

TESTIMONIAL_CHANNEL_ID = os.environ.get("TESTIMONIAL_CHANNEL_ID", "")

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get_random_banner() -> Optional[Path]:
    # Try finding banner files matching file_*.png under attached_assets
    banners = list(Path("/app/attached_assets").glob("file_*.png"))
    if not banners:
        banners = list(Path("attached_assets").glob("file_*.png"))
    if banners:
        return random.choice(banners)
    return None


def get_qris_path() -> Optional[Path]:
    paths = [
        Path("/app/attached_assets/qris.jpeg"),
        Path("attached_assets/qris.jpeg"),
        Path("../attached_assets/qris.jpeg")
    ]
    for p in paths:
        if p.exists():
            return p
    return None


async def post_to_testimonial_channel(bot, text: str):
    channel_id = TESTIMONIAL_CHANNEL_ID or ADMIN_CHAT
    if not channel_id:
        logger.warning("No TESTIMONIAL_CHANNEL_ID or ADMIN_CHAT configured, skipping testimonial post.")
        return
    banner = get_random_banner()
    try:
        if banner and banner.exists():
            with open(banner, "rb") as photo:
                await bot.send_photo(
                    chat_id=channel_id,
                    photo=photo,
                    caption=text,
                    parse_mode="Markdown"
                )
            logger.info(f"Testimonial sent to channel {channel_id} with banner {banner.name}")
        else:
            await bot.send_message(
                chat_id=channel_id,
                text=text,
                parse_mode="Markdown"
            )
            logger.info(f"Testimonial sent to channel {channel_id} without banner")
    except Exception as e:
        logger.error(f"Failed to post testimonial to channel {channel_id}: {e}")


async def main_menu_keyboard(chat_id: int):
    rows = []
    # Nomor Management
    rows.append([
        InlineKeyboardButton("📱 Cek Nomor WA", callback_data="num_info"),
        InlineKeyboardButton("🔍 Cari Prefix", callback_data="search_prompt")
    ])
    checker_connected = await asyncio.to_thread(is_checker_connected, chat_id)
    checker_label = "🔗 WA Checker: Terhubung ✅" if checker_connected else "🔗 Connect WA Checker"
    rows.append([InlineKeyboardButton(checker_label, callback_data="connect_wa_info")])
    # Email temp
    rows.append([InlineKeyboardButton("📧 Email Temp (Receive Only)",      callback_data="gen")])
    rows.append([InlineKeyboardButton("📋 Pilih Provider Email Temp",      callback_data="menu_provider")])
    # SMTP Auto & Manual
    rows.append([InlineKeyboardButton("🤖 Auto Generate SMTP",             callback_data="autogen_smtp")])
    rows.append([InlineKeyboardButton("➕ Tambah SMTP Manual",              callback_data="add_smtp_info")])
    rows.append([InlineKeyboardButton("📂 Akun SMTP",                      callback_data="list_smtp")])
    # WhatsApp Fix
    rows.append([InlineKeyboardButton("🔧 WhatsApp Fix (/fix +nomor)",     callback_data="fix_info")])
    # iVasms Temp Numbers
    rows.append([InlineKeyboardButton("🌍 iVasms Temp Numbers (OTP)",       callback_data="ivasms_main")])
    # Update & Info
    rows.append([InlineKeyboardButton("🔄 Cek & Update Bot",               callback_data="check_update")])
    rows.append([InlineKeyboardButton("❓ Cara Pakai",  callback_data="howto"),
                 InlineKeyboardButton("📊 Status Bot",  callback_data="status")])
    rows.append([InlineKeyboardButton("💖 Donasi Developer",             callback_data="donasi")])
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


# ── Formatters ──────────────────────────────────────────────────────────�[...]
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


def fmt_number_results(results: List[Dict], total_in_file: int) -> str:
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


def build_number_buttons(results: List[Dict], chat_id: int) -> InlineKeyboardMarkup:
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


# ── Command Handlers ────────────────────────────────────────────────────────�[...]
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    checker_connected = await asyncio.to_thread(is_checker_connected, chat_id)
    checker_status = "✅ WA Checker terhubung" if checker_connected else "⚠️ WA Checker belum diset"
    reply_markup_kb = await main_menu_keyboard(chat_id)
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
        reply_markup=reply_markup_kb,
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

    chat_id = update.effective_chat.id
    msg = await update.message.reply_text(f"🔄 Verifikasi SMTP `{email}`...", parse_mode="Markdown")
    result = await asyncio.to_thread(manager.add_account, email, password, chat_id)
    text   = fmt_smtp_add_ok(result) if result["success"] else fmt_smtp_add_fail(result)
    kb     = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Lihat Akun", callback_data="list_smtp")],
        [InlineKeyboardButton("🏠 Menu Utama", callback_data="back_main")],
    ])
    await msg.edit_text(text, parse_mode="Markdown", reply_markup=kb)


# (rest of file unchanged)
