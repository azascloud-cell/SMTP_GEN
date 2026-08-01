# 🚀 Deploy API Email ke InfinityFree

> Lakukan ini SATU KALI. Setelah deploy, bot langsung bisa generate SMTP real.

---

## Kenapa Perlu Deploy?

GitHub Actions diblokir Cloudflare InfinityFree di level network.
Solusinya: taruh PHP script kecil di dalam hosting InfinityFree sendiri.
Script itu punya akses ke cPanel via `localhost` — bot tinggal panggil via HTTP.

```
Bot (GitHub Actions) ──HTTP──► api_email.php (InfinityFree) ──localhost──► cPanel API
```

---

## Langkah Deploy

### 1. Upload `api_email.php`

Buka **cPanel InfinityFree** → **File Manager** → masuk ke folder `htdocs` / `public_html`

Upload file `api_email.php` dari folder `hosting/` di repo ini.

> Atau via FTP: host = `ftpupload.net`, user & pass = kredensial cPanel kamu

---

### 2. Set Konfigurasi di File

Edit `api_email.php` yang sudah diupload, cari baris ini:

```php
define('CPANEL_PASS', getenv('CPANEL_PASSWORD') ?: '');
define('API_KEY',     getenv('API_SECRET_KEY')  ?: '');
```

Ganti menjadi (isi nilainya langsung):

```php
define('CPANEL_PASS', 'PASSWORD_CPANEL_KAMU');
define('API_KEY',     'BUAT_KUNCI_RAHASIA_RANDOM');  // contoh: Xk9mPqL2rNz7vWe
```

**Simpan.**

---

### 3. Test API

Buka di browser (ganti `smtpgen.xo.je` dan `KUNCI` sesuai milikmu):

```
https://smtpgen.xo.je/api_email.php?key=KUNCI&action=ping
```

Harus muncul:
```json
{"ok":true,"msg":"API aktif","domain":"smtpgen.xo.je"}
```

---

### 4. Set GitHub Secrets

Buka repo GitHub → **Settings** → **Secrets and variables** → **Actions**

| Secret Name | Nilai |
|-------------|-------|
| `CPANEL_API_URL` | `https://smtpgen.xo.je/api_email.php` |
| `CPANEL_API_KEY` | kunci rahasia yang kamu buat di langkah 2 |
| `CPANEL_DOMAIN`  | `smtpgen.xo.je` |

> ⚠️ `CPANEL_USER` dan `CPANEL_PASS` tidak perlu lagi — sudah ada di PHP script.

---

### 5. Restart Bot

Di GitHub → tab **Actions** → **🤖 SMTP Generator Bot** → **Run workflow**

Selesai! Coba `/cpanelgen` di Telegram.

---

## Troubleshooting

**`{"ok":false,"error":"Unauthorized"}`**
→ `API_KEY` di PHP tidak sama dengan `CPANEL_API_KEY` di GitHub Secrets

**`{"ok":false,"error":"CPANEL_USER / CPANEL_PASS belum dikonfigurasi"}`**
→ Edit `api_email.php`, isi `CPANEL_PASS` dengan password cPanel kamu

**`{"ok":false,"error":"Tidak bisa connect ke cPanel localhost."}`**
→ Pastikan file ada di `public_html/` (bukan di subfolder lain)
→ Coba akses `/api_email.php?key=...&action=ping` dulu

**File tidak ditemukan (404)**
→ Upload ke folder `htdocs` atau `public_html`, bukan root
