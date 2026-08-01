# 🤖 SMTP Generator Bot

Bot Telegram yang **generate SMTP credentials otomatis** dari berbagai provider email sementara (temporary email). Bot berjalan 24/7 via **GitHub Actions** dengan mekanisme **Keep-Alive** otomatis.

---

## ✨ Fitur

| Fitur | Keterangan |
|-------|------------|
| 📧 Auto Generate | Generate email + SMTP credentials dalam 1 klik |
| 🔧 Multi Provider | 1SecMail, GuerrillaMail, Mail.tm, TempMail, Dispostable |
| 🔄 Keep-Alive | Restart otomatis setiap 5 jam agar tetap 24/7 |
| 🔔 Notif Restart | Notifikasi ke Telegram saat bot aktif kembali |
| 📋 Inline Keyboard | UI interaktif tanpa perlu ketik command |

---

## 🚀 Setup & Deployment

### 1. Fork / Clone repo ini

```bash
git clone https://github.com/YOUR_USERNAME/SMTP_GEN.git
cd SMTP_GEN
```

### 2. Buat Telegram Bot

1. Buka [@BotFather](https://t.me/BotFather) di Telegram
2. Ketik `/newbot` dan ikuti instruksinya
3. Salin **Bot Token** yang diberikan

### 3. Dapatkan Chat ID kamu

1. Kirim pesan ke bot kamu
2. Buka: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Salin `chat.id` dari response JSON

### 4. Set GitHub Secrets

Buka repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret Name | Nilai |
|-------------|-------|
| `TELEGRAM_BOT_TOKEN` | Token dari BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID kamu (untuk notifikasi restart) |
| `GH_PAT` | GitHub Personal Access Token (scope: `workflow`) |

#### Cara buat GH_PAT:
1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. **Generate new token** → centang scope **`workflow`**
3. Salin token, masukkan sebagai secret `GH_PAT`

### 5. Aktifkan GitHub Actions

1. Buka tab **Actions** di repo
2. Enable workflows jika diminta
3. Jalankan manual: **Actions** → **🤖 SMTP Generator Bot** → **Run workflow**

---

## ⚙️ Cara Kerja Keep-Alive

```
[Bot Workflow]  ─── berjalan max 5.5 jam ───►  selesai / crash
      ▲
      │  trigger (via GitHub API)
      │
[Keepalive Workflow]  ─── cron setiap 5 jam ───►  restart bot
      │
      └──► kirim notifikasi Telegram: "Bot Direstart"
```

- **`bot.yml`** — workflow utama yang menjalankan bot (polling Telegram)  
- **`keepalive.yml`** — scheduler cron yang trigger ulang `bot.yml` setiap 5 jam  
- Saat bot baru aktif, otomatis kirim pesan notifikasi ke `TELEGRAM_CHAT_ID`

---

## 📋 Command Bot

| Command | Fungsi |
|---------|--------|
| `/start` | Menu utama dengan tombol interaktif |
| `/generate` | Generate SMTP credentials langsung |
| `/status` | Cek status bot & provider aktif |
| `/help` | Panduan cara pakai |

---

## 🔧 Provider Email

| Provider | Domain | Keterangan |
|----------|--------|------------|
| **1SecMail** | 1secmail.com/net/org | API publik, langsung aktif |
| **GuerrillaMail** | guerrillamail.com | API publik, populer |
| **Mail.tm** | mail.tm | Buat akun real via API |
| **TempMail** | tempmail.com, yopmail.com | Domain disposable |
| **Dispostable** | maildrop.cc, dll | Domain throwaway |

---

## ⚠️ Disclaimer

> Bot ini dibuat untuk keperluan **testing**, **development**, dan **privasi**. 
> Jangan gunakan untuk spam, phishing, atau aktivitas ilegal.
> Email yang digenerate bersifat **publik dan sementara** — jangan simpan data sensitif di dalamnya.

---

## 📄 Lisensi

MIT License — bebas digunakan dan dimodifikasi.
