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

// Global socket and connection state variables to avoid duplicate handler bug on reconnect
let sock = null;
let isConnected = false;

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
    const currentSock = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: true
    });

    // Update global reference
    sock = currentSock;

    currentSock.ev.on('creds.update', saveCreds);

    currentSock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            console.log("=========================================");
            console.log("QR CODE AVAILABLE. PLEASE SCAN IN TERMINAL.");
            console.log("=========================================");
        }

        if (connection === 'close') {
            isConnected = false;
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection closed due to', lastDisconnect?.error, ', reconnecting:', shouldReconnect);
            if (shouldReconnect) {
                setTimeout(() => {
                    startWA();
                }, 3000);
            }
        } else if (connection === 'open') {
            isConnected = true;
            console.log("WhatsApp connection is open and active!");
            sendTelegramMessage("🔗 *WhatsApp Checker: Terhubung!* ✅\nBot siap melakukan pengecekan nomor.");
        }
    });

    // Handle Pairing Code via phone number
    const pairingNumber = process.env.PAIRING_PHONE_NUMBER;
    if (pairingNumber && !currentSock.authState.creds.registered) {
        console.log(`Attempting pairing with: ${pairingNumber}`);
        setTimeout(async () => {
            try {
                if (currentSock.authState.creds.registered) return;

                // Request pairing code
                const code = await currentSock.requestPairingCode(pairingNumber.replace(/[^\d]/g, ''));
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
}

// Start WhatsApp connection
startWA();

// Define Express routes once outside startWA() to avoid duplicate listener registration
app.get('/pair', async (req, res) => {
    const phone = req.query.phone;
    if (!phone) {
        return res.status(400).json({ error: "Missing phone parameter" });
    }
    if (!sock) {
        return res.status(503).json({ error: "WA Checker is starting up, please try again." });
    }
    if (sock.authState.creds.registered) {
        return res.status(400).json({ error: "WA Checker is already linked/registered." });
    }
    try {
        const cleanPhone = phone.replace(/[^\d]/g, '');
        console.log(`Requesting pairing code for: ${cleanPhone}`);
        const code = await sock.requestPairingCode(cleanPhone);
        console.log(`PAIRING CODE GENERATED: ${code}`);
        return res.json({ success: true, code: code, phone: cleanPhone });
    } catch (err) {
        console.error("Failed to request pairing code:", err);
        return res.status(500).json({ error: err.message });
    }
});

app.get('/check', async (req, res) => {
    const phone = req.query.phone;
    if (!phone) {
        return res.status(400).json({ error: "Missing phone parameter" });
    }

    if (!sock || !sock.authState || !sock.authState.creds.registered) {
        return res.status(503).json({ registered: null, error: "WA Checker is not linked/authenticated yet." });
    }

    if (!isConnected) {
        return res.status(503).json({ registered: null, error: "WA Checker is linked but connection to WhatsApp is currently closed/reconnecting. Please wait." });
    }

    try {
        // Normalize number format for JID (digits only + JID suffix)
        const cleanPhone = phone.replace(/[^\d]/g, '');
        const jid = `${cleanPhone}@s.whatsapp.net`;

        console.log(`Checking registration for: ${cleanPhone}`);
        let results = await sock.onWhatsApp(jid);

        // Fallback: try cleanPhone without suffix if results is empty or undefined
        if (!results || results.length === 0) {
            results = await sock.onWhatsApp(cleanPhone);
        }

        const registered = results && results.length > 0 && (results[0].exists || results[0].exists === undefined);
        console.log(`Result for ${cleanPhone}: registered = ${!!registered}`);
        return res.json({ registered: !!registered });
    } catch (e) {
        console.error("Error querying WhatsApp registration:", e);
        return res.status(500).json({ registered: null, error: e.message });
    }
});

app.listen(port, () => {
    console.log(`WA Checker Server listening on port ${port}`);
});
