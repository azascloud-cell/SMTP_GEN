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
| 🔗 QR Pairing | Tautkan WhatsApp via QR code scan |

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
| `/pair` | Tautkan WA Checker via pairing code |
| `/qr` | Tautkan WA Checker via QR code |
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

## 🎮 Cara Deploy ke Panel Pterodactyl (Tanpa GitHub Actions)

Jika Anda ingin menjalankan bot secara mandiri pada hosting panel Pterodactyl agar tidak bergantung pada GitHub Actions (24/7 tanpa batas limit waktu workflow):

### 1. Persiapan File
1. Download source code bot ini dalam format `.zip` dari repository GitHub Anda.
2. Upload file `.zip` tersebut ke File Manager di panel Pterodactyl Anda, lalu extract seluruh file di directory root (`/home/container`).

### 2. Konfigurasi Environment (Startup Settings)
Pada menu **Startup** di panel Pterodactyl Anda, tambahkan Environment Variables berikut:

| Key | Value / Contoh | Keterangan |
|-----|----------------|------------|
| `TELEGRAM_BOT_TOKEN` | `123456:ABC-DEF...` | Token Bot Telegram Anda dari @BotFather |
| `TELEGRAM_CHAT_ID` | `987654321` | ID Chat Telegram Anda untuk notifikasi |
| `GH_PAT` | `ghp_xxxxxx` | GitHub Personal Access Token (Opsional, untuk auto-sync storage) |
| `GITHUB_REPOSITORY` | `username/repo` | Nama repo GitHub Anda (Opsional, untuk auto-sync storage) |

### 3. Setup Node.js & Python di Container (Egg)
Karena bot ini menggunakan Python (untuk Bot Telegram) dan Node.js (untuk WA Checker), pastikan Anda memilih **Egg** (Docker Image) yang tepat:

* **Rekomendasi:** Gunakan Egg **Python** (versi `3.10`, `3.11` atau `3.12`).
* Jika panel hosting Anda mendukung, gunakan Docker Image multi-language (yang menyertakan `node` dan `python`).
* Di File Manager, edit file `requirements.txt` dan pastikan isinya:
  ```text
  python-telegram-bot==21.5
  requests==2.32.3
  beautifulsoup4==4.15.0
  soupsieve==2.9.1
  openpyxl==3.1.5
  ```

### 4. Menjalankan WhatsApp Checker (Baileys) secara Otomatis
Untuk menjalankan file `wa_checker.js` secara otomatis di latar belakang sebelum bot utama Python berjalan, buat file bash startup kustom.
1. Di File Manager, buat file baru bernama `start.sh` (jika belum ada) dan isi dengan kode berikut:
   ```bash
   #!/bin/bash
   echo "Installing NodeJS dependencies..."
   npm install @whiskeysockets/baileys express qrcode-terminal pino dotenv

   echo "Starting WhatsApp Checker (NodeJS) in background..."
   node wa_checker.js > wa_checker.log 2>&1 &

   echo "Installing Python dependencies..."
   pip install -r requirements.txt

   echo "Starting Telegram Bot..."
   python bot/main.py
   ```
2. Pada menu **Startup** Pterodactyl, ubah bagian **Startup Command** menjadi:
   ```bash
   bash start.sh
   ```
3. Tekan tombol **Console** lalu klik **Start / Restart** server Anda. Pterodactyl akan secara otomatis menginstal seluruh dependency NodeJS & Python, lalu menjalankan bot secara 24/7!

---

## 📄 Lisensi

MIT License — bebas digunakan dan dimodifikasi.
