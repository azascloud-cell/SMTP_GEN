# 🔧 Panduan Setup Hosting Gratis untuk Generate SMTP Real

Panduan ini menjelaskan cara setup InfinityFree (hosting gratis) agar bot bisa
generate email SMTP yang benar-benar bisa digunakan untuk kirim & terima email —
persis seperti cara kerja bot Fix Merah Yanskie.

---

## Mengapa Perlu Hosting?

Bot generate **email SMTP real** dengan cara membuat akun email di domain hosting.
Email yang dibuat punya server SMTP nyata (`mail.domainmu.epizy.com`) yang
menerima koneksi dengan username + password asli.

---

## Langkah 1 — Daftar InfinityFree (Gratis)

1. Buka **https://infinityfree.net**
2. Klik **Sign Up** → isi email & password
3. Verifikasi email
4. Login ke dashboard

---

## Langkah 2 — Buat Hosting Account

1. Di dashboard, klik **+ Create Account**
2. Pilih subdomain gratis, contoh:
   - `namakamu.epizy.com`
   - `namakamu.rf.gd`
3. Isi password untuk hosting account ini
4. Klik **Create Account**

---

## Langkah 3 — Catat Kredensial cPanel

Setelah akun dibuat, pergi ke **Control Panel** (cPanel):

| Info yang dibutuhkan | Di mana menemukannya |
|---------------------|----------------------|
| **cPanel URL** | Di dashboard InfinityFree → tombol "Control Panel" → salin URL-nya (biasanya `https://cpanel.epizy.com`) |
| **cPanel Username** | Tertera di dashboard (biasanya dimulai angka, contoh: `epiz_12345678`) |
| **cPanel Password** | Password yang kamu isi saat buat hosting account |
| **Domain** | Subdomain yang kamu pilih, contoh: `namakamu.epizy.com` |

---

## Langkah 4 — Set GitHub Secrets

Buka repo GitHub → **Settings** → **Secrets and variables** → **Actions**

Tambahkan 4 secrets berikut:

| Secret Name | Contoh Nilai |
|-------------|-------------|
| `CPANEL_URL` | `https://cpanel.epizy.com` |
| `CPANEL_USER` | `epiz_12345678` |
| `CPANEL_PASS` | `passwordHostingmu` |
| `CPANEL_DOMAIN` | `namakamu.epizy.com` |

---

## Langkah 5 — Restart Bot

1. Buka tab **Actions** di repo
2. Pilih **🤖 SMTP Generator Bot**
3. Klik **Run workflow** → **Run workflow**

Bot akan restart dan otomatis terdeteksi konfigurasi cPanel-nya.

---

## Hasil Setelah Setup

Setelah setup selesai, bot bisa generate email seperti ini:

```
✅ Email SMTP Real Berhasil Dibuat!
━━━━━━━━━━━━━━━━━━━━
📧 Email:     abc123@namakamu.epizy.com
🔑 Password:  Xk9#mPqL2rNz!vWe
━━━━━━━━━━━━━━━━━━━━
📤 SMTP Host: mail.namakamu.epizy.com
🔌 SMTP Port: 587 (STARTTLS) / 465 (SSL)
📥 IMAP Host: mail.namakamu.epizy.com
🔌 IMAP Port: 993
━━━━━━━━━━━━━━━━━━━━
✅ Akun ini bisa kirim & terima email nyata!
```

---

## Alternatif Hosting Gratis Lain

| Hosting | Domain Gratis | cPanel | Email |
|---------|--------------|--------|-------|
| **InfinityFree** | ✅ `.epizy.com` / `.rf.gd` | ✅ Ya | ✅ Ya |
| **Freehostia** | ✅ `.freehostia.com` | ✅ Ya | ✅ Ya |
| **000webhost** | ⚠️ Terbatas | ⚠️ Terbatas | ❌ Tidak |
| **GitHub Pages** | ✅ `.github.io` | ❌ Tidak | ❌ Tidak |

> ⚠️ **GitHub Pages tidak bisa** digunakan untuk email — itu hanya untuk file statis.

---

## Troubleshooting

**Error: "Tidak bisa connect ke cPanel"**
- Pastikan `CPANEL_URL` diawali `https://` dan tidak ada `/` di akhir
- Coba ganti port: `https://cpanel.epizy.com:2083`

**Error: "Login ditolak"**
- Pastikan username & password cPanel benar (bukan password email InfinityFree)

**Error: "domain tidak valid"**
- `CPANEL_DOMAIN` harus sama persis dengan subdomain yang terdaftar
