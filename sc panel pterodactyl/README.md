# SC Panel Pterodactyl

WhatsApp Checker (Baileys) — versi murni Node.js untuk Pterodactyl Panel.

## Jalankan

```bash
npm install
node wa_checker.js
```

Server HTTP berjalan di port 3000 (atau set via `PORT`).

## Endpoint

- `GET /pair?phone=628xxx&chat_id=123` — kode pairing
- `GET /check?phone=628xxx&chat_id=123` — cek nomor WA
- `GET /status?chat_id=123` — status koneksi

## Environment

- `TELEGRAM_BOT_TOKEN` — token bot Telegram (kunci enkripsi sesi)
- `TELEGRAM_CHAT_ID` — chat ID notifikasi
- `PORT` — port HTTP (default 3000)
