# SMTP Generator Bot — Pterodactyl Panel

## Cara memasang

1. Gunakan image Pterodactyl yang menyediakan Python 3.11/3.12, Node.js 20, npm, bash, unzip, dan openssl.
2. Upload `sc panel pterodactyl.zip` ke folder utama server, lalu extract.
3. Set Startup Command menjadi `bash start.sh`.
4. Isi `TELEGRAM_BOT_TOKEN` sebagai variabel wajib.
5. Variabel tambahan: `TELEGRAM_CHAT_ID`, `PAIRING_PHONE_NUMBER`, `GH_PAT`, `GITHUB_REPOSITORY`, `MAILERSEND_API_KEY`, `MAILERSEND_SENDER_EMAIL`, `CPANEL_API_URL`, `CPANEL_API_KEY`, `CPANEL_DOMAIN`, `CPANEL_URL`, `CPANEL_USER`, dan `CPANEL_PASS` sesuai fitur yang digunakan.
6. Start server. Dependency Node.js dan Python akan dipasang otomatis.

Jangan memasukkan token, password, atau API key ke dalam file zip. File sesi WhatsApp terenkripsi bersifat rahasia. Gunakan bot hanya untuk testing, development, dan penggunaan yang sah; jangan gunakan untuk spam, phishing, atau aktivitas ilegal.
