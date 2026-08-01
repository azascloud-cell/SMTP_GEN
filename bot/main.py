import os
import asyncio
import logging
from datetime import datetime, timezone
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from smtp_generator import SMTPGenerator
from smtp_manager import SMTPManager
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
        "📋 *Commands:*\n"
        "/start — Menu utama\n"
        "/generate — Email temp\n"
        "/cpanelgen — Generate SMTP real\n"
        "/addsmtp — Tambah SMTP manual\n"
        "/listsmtp — Lihat akun manual\n"
        "/delsmtp — Hapus akun manual\n"
        "/cpanelsetup — Panduan setup hosting\n"
        "/status — Status bot",
        parse_mode="MarkdownV2",
    )


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
            BotCommand("cpanelsetup", "🔧 Panduan setup hosting gratis"),
            BotCommand("testcpanel",  "🔍 Diagnostik DNS + panel"),
            BotCommand("status",      "📊 Status bot"),
            BotCommand("help",        "❓ Bantuan"),
        ])

    app.post_init = post_init
    logger.info("Bot mulai polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
