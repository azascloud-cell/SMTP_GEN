const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const express = require('express');
const pino = require('pino');
const https = require('https');
const path = require('path');
const fs = require('fs');

const app = express();
const port = process.env.PORT || 3000;

// Ensure directories exist
const authDir = path.join(__dirname, 'data', 'baileys_auth');
fs.mkdirSync(authDir, { recursive: true });

function sendTelegramMessage(text) {
    const token = process.env.TELEGRAM_BOT_TOKEN;
    const chatId = process.env.TELEGRAM_CHAT_ID;
    if (!token || !chatId) {
        console.log("Telegram config missing, skipping notification.");
        return;
    }

    const payload = JSON.stringify({
        chat_id: chatId,
        text: text,
        parse_mode: 'Markdown'
    });

    const options = {
        hostname: 'api.telegram.org',
        port: 443,
        path: `/bot${token}/sendMessage`,
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payload)
        }
    };

    const req = https.request(options, (res) => {
        let responseBody = '';
        res.on('data', (chunk) => { responseBody += chunk; });
        res.on('end', () => {
            console.log("Telegram notify response:", responseBody);
        });
    });

    req.on('error', (e) => {
        console.error("Failed to notify Telegram:", e);
    });

    req.write(payload);
    req.end();
}

async function startWA() {
    const { state, saveCreds } = await useMultiFileAuthState(authDir);
    const sock = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: true
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            console.log("=========================================");
            console.log("QR CODE AVAILABLE. PLEASE SCAN IN TERMINAL.");
            console.log("=========================================");
        }

        if (connection === 'close') {
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection closed due to', lastDisconnect?.error, ', reconnecting:', shouldReconnect);
            if (shouldReconnect) {
                startWA();
            }
        } else if (connection === 'open') {
            console.log("WhatsApp connection is open and active!");
            sendTelegramMessage("🔗 *WhatsApp Checker: Terhubung!* ✅\nBot siap melakukan pengecekan nomor.");
        }
    });

    // Handle Pairing Code via phone number
    const pairingNumber = process.env.PAIRING_PHONE_NUMBER;
    if (pairingNumber && !sock.authState.creds.registered) {
        console.log(`Attempting pairing with: ${pairingNumber}`);
        setTimeout(async () => {
            try {
                // Request pairing code
                const code = await sock.requestPairingCode(pairingNumber.replace(/[^\d]/g, ''));
                console.log("=========================================");
                console.log(`PAIRING CODE GENERATED: ${code}`);
                console.log("=========================================");
                sendTelegramMessage(
                    `🔗 *WhatsApp Pairing Code:*\n` +
                    `━━━━━━━━━━━━━━━━━━━━\n` +
                    `🔑 Code: \`${code}\`\n` +
                    `📱 Nomor: \`${pairingNumber}\`\n\n` +
                    `Silakan buka WhatsApp Anda -> perangkat tertaut -> tautkan perangkat -> tautkan dengan nomor telepon, lalu masukkan kode di atas.`
                );
            } catch (err) {
                console.error("Failed to request pairing code:", err);
            }
        }, 6000);
    }

    app.get('/check', async (req, res) => {
        const phone = req.query.phone;
        if (!phone) {
            return res.status(400).json({ error: "Missing phone parameter" });
        }

        if (!sock.authState.creds.registered) {
            return res.status(503).json({ registered: null, error: "WA Checker is not linked/authenticated yet." });
        }

        try {
            // Normalize number format for JID (digits only + JID suffix)
            const cleanPhone = phone.replace(/[^\d]/g, '');
            const jid = `${cleanPhone}@s.whatsapp.net`;

            console.log(`Checking registration for: ${cleanPhone}`);
            const results = await sock.onWhatsApp(jid);

            const registered = results && results.length > 0 && results[0].exists;
            return res.json({ registered: !!registered });
        } catch (e) {
            console.error("Error querying WhatsApp registration:", e);
            return res.status(500).json({ registered: null, error: e.message });
        }
    });
}

startWA();

app.listen(port, () => {
    console.log(`WA Checker Server listening on port ${port}`);
});
