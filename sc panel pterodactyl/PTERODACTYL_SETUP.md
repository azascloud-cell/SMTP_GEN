# SC Panel Pterodactyl — Node.js Only

Paket ini hanya berisi komponen Node.js: **WhatsApp Checker (Baileys)**. Semua fitur Python (Telegram Bot, SMTP Generator, cPanel Generator, Number Manager, WhatsApp Fix, iVasms, AI Agent) telah dihapus karena panel Anda hanya mendukung Node.js.

## Isi paket

| File | Keterangan |
|---|---|
| `wa_checker.js` | WhatsApp Checker berbasis Baileys — server HTTP di port 3000 |
| `package.json` | Dependency Node.js (Baileys, Express, pino, qrcode-terminal) |
| `start.sh` | Startup script untuk Pterodactyl |
| `data/` | Folder untuk sesi WhatsApp |

## Endpoint HTTP

| Endpoint | Method | Parameter | Keterangan |
|---|---|---|---|
| `/pair` | GET | `phone`, `chat_id` | Membuat kode pairing WhatsApp |
| `/check` | GET | `phone`, `chat_id` | Cek apakah nomor terdaftar di WhatsApp |
| `/status` | GET | `chat_id` | Cek status koneksi WhatsApp |

## Cara pasang

1. Gunakan Egg **Node.js** (versi 18 atau 20) di Pterodactyl.
2. Upload `sc panel pterodactyl.zip` ke folder utama server.
3. Extract zip di folder utama server.
4. Pada **Startup Command**, isi:

```
bash start.sh
```

5. Tambahkan environment variables di menu **Startup**:

| Nama | Wajib | Keterangan |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | Ya | Token bot Telegram (dipakai juga sebagai kunci enkripsi sesi) |
| `TELEGRAM_CHAT_ID` | Tidak | Chat ID untuk notifikasi Telegram |
| `PAIRING_PHONE_NUMBER` | Tidak | Nomor WhatsApp untuk pairing otomatis |
| `PORT` | Tidak | Port HTTP server (default 3000) |

6. Start atau restart server. Dependency Node.js dipasang otomatis.

## Catatan

- Sesi WhatsApp disimpan di folder `data/baileys_auth_*`.
- Sesi terenkripsi (`data/baileys_auths.enc`) dipulihkan otomatis saat startup jika `TELEGRAM_BOT_TOKEN` tersedia.
- Tanpa Python, bot Telegram utama tidak berjalan. WhatsApp Checker tetap berfungsi sebagai server HTTP untuk pengecekan nomor dan kode pairing.
