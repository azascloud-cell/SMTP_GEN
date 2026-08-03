const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys');
const express = require('express');
const pino = require('pino');
const https = require('https');
const path = require('path');
const fs = require('fs');
const { exec } = require('child_process');

const app = express();
const port = process.env.PORT || 3000;

// Sessions pool: { [chatId]: { sock, isConnected, authDir } }
const sessions = {};

// Session Auto-Persistence to Git to prevent session loss on restart
let saveSessionTimeout = null;

function saveSessionToGit() {
    console.log("Saving encrypted Baileys sessions to Git repository...");
    const cmd = `
        # Check if any user credentials directories exist
        if ! ls -d data/baileys_auth_* >/dev/null 2>&1; then
            echo "No user credentials directories found under data/baileys_auth_*, skipping save."
            exit 0
        fi

        # Zip and encrypt all user auth directories using TELEGRAM_BOT_TOKEN as the passphrase
        zip -q -r data/baileys_auths.zip data/baileys_auth_*
        openssl enc -aes-256-cbc -salt -pbkdf2 -in data/baileys_auths.zip -out data/baileys_auths.enc -pass pass:"$TELEGRAM_BOT_TOKEN"
        rm -f data/baileys_auths.zip

        git config --global user.name "github-actions[bot]"
        git config --global user.email "github-actions[bot]@users.noreply.github.com"
        git add data/baileys_auths.enc

        if [ -n "$(git status --porcelain data/baileys_auths.enc)" ]; then
            git commit -m "chore: update encrypted whatsapp sessions [skip ci]"
            git pull --rebase origin main || true
            git push origin HEAD:main
            echo "Encrypted session state successfully pushed to Git!"
        else
            echo "No changes in encrypted session state."
        fi
    `;
    exec(cmd, (err, stdout, stderr) => {
        if (err) {
            console.error("Failed to save session to Git:", err);
            return;
        }
        console.log("saveSessionToGit output:", stdout);
        if (stderr) console.error("saveSessionToGit stderr:", stderr);
    });
}

function debouncedSaveSession() {
    if (saveSessionTimeout) clearTimeout(saveSessionTimeout);
    saveSessionTimeout = setTimeout(() => {
        saveSessionToGit();
    }, 15000); // 15s debounce to group startup/pairing writes together
}

function sendTelegramMessage(chatId, text) {
    const token = process.env.TELEGRAM_BOT_TOKEN;
    const targetChatId = chatId || process.env.TELEGRAM_CHAT_ID;
    if (!token || !targetChatId) {
        console.log("Telegram config missing, skipping notification.");
        return;
    }

    const payload = JSON.stringify({
        chat_id: targetChatId,
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

async function getOrCreateSocket(chatId, pairingNumber = null) {
    if (!chatId) return null;

    // Return existing socket if active
    if (sessions[chatId]) {
        if (pairingNumber && !sessions[chatId].sock.authState.creds.registered) {
            // Re-trigger pairing logic on existing if requested
            triggerPairing(sessions[chatId].sock, pairingNumber, chatId);
        }
        return sessions[chatId];
    }

    const authDir = path.join(__dirname, 'data', `baileys_auth_${chatId}`);
    fs.mkdirSync(authDir, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(authDir);
    const sock = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: false
    });

    sessions[chatId] = {
        sock,
        isConnected: false,
        authDir
    };

    sock.ev.on('creds.update', () => {
        saveCreds();
        debouncedSaveSession();
    });

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect } = update;

        if (connection === 'close') {
            sessions[chatId].isConnected = false;
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log(`[Chat ${chatId}] Connection closed due to`, lastDisconnect?.error, `, reconnecting:`, shouldReconnect);
            if (shouldReconnect) {
                setTimeout(() => {
                    getOrCreateSocket(chatId);
                }, 3000);
            } else {
                // If logged out, delete the directory and remove session
                console.log(`[Chat ${chatId}] Logged out. Cleaning up credentials...`);
                try {
                    fs.rmSync(authDir, { recursive: true, force: true });
                } catch (e) {
                    console.error("Failed to delete auth dir:", e);
                }
                delete sessions[chatId];
                sendTelegramMessage(chatId, "⚠️ *Koneksi WhatsApp Terputus!* Session Anda telah di-logout.");
                debouncedSaveSession();
            }
        } else if (connection === 'open') {
            sessions[chatId].isConnected = true;
            console.log(`[Chat ${chatId}] WhatsApp connection is open and active!`);
            sendTelegramMessage(chatId, "🔗 *WhatsApp Checker: Terhubung!* ✅\nSession Anda siap digunakan untuk pengecekan nomor.");
            debouncedSaveSession();
        }
    });

    if (pairingNumber) {
        triggerPairing(sock, pairingNumber, chatId);
    }

    return sessions[chatId];
}

async function triggerPairing(sock, pairingNumber, chatId) {
    if (!sock.authState.creds.registered) {
        console.log(`[Chat ${chatId}] Attempting pairing with: ${pairingNumber}`);
        setTimeout(async () => {
            try {
                if (sock.authState.creds.registered) return;
                const cleanPhone = pairingNumber.replace(/[^\d]/g, '');
                const code = await sock.requestPairingCode(cleanPhone);
                console.log(`[Chat ${chatId}] PAIRING CODE GENERATED: ${code}`);
                sendTelegramMessage(
                    chatId,
                    `🔗 *WhatsApp Pairing Code Anda:*\n` +
                    `━━━━━━━━━━━━━━━━━━━━\n` +
                    `🔑 Code: \`${code}\`\n` +
                    `📱 Nomor: \`${pairingNumber}\`\n\n` +
                    `Silakan buka WhatsApp Anda -> perangkat tertaut -> tautkan perangkat -> tautkan dengan nomor telepon, lalu masukkan kode di atas.`
                );
            } catch (err) {
                console.error(`[Chat ${chatId}] Failed to request pairing code:`, err);
            }
        }, 3000);
    }
}

// Startup routine: automatically initialize and restore all existing sessions
function initExistingSessions() {
    console.log("Initializing pre-existing user WhatsApp sessions...");
    const dataDir = path.join(__dirname, 'data');
    if (fs.existsSync(dataDir)) {
        const files = fs.readdirSync(dataDir);
        files.forEach(file => {
            if (file.startsWith('baileys_auth_')) {
                const chatId = file.substring('baileys_auth_'.length);
                if (chatId) {
                    console.log(`Restoring WhatsApp session for chat ID: ${chatId}`);
                    getOrCreateSocket(chatId);
                }
            }
        });
    }
}

// Start existing sessions
initExistingSessions();

// Express endpoints
app.get('/pair', async (req, res) => {
    const phone = req.query.phone;
    const chatId = req.query.chat_id || process.env.TELEGRAM_CHAT_ID;
    if (!phone) {
        return res.status(400).json({ error: "Missing phone parameter" });
    }
    if (!chatId) {
        return res.status(400).json({ error: "Missing chat_id parameter" });
    }

    try {
        const session = await getOrCreateSocket(chatId, phone);
        if (session.sock.authState.creds.registered) {
            return res.status(400).json({ error: "WhatsApp session is already linked for this user." });
        }
        const cleanPhone = phone.replace(/[^\d]/g, '');
        const code = await session.sock.requestPairingCode(cleanPhone);
        return res.json({ success: true, code: code, phone: cleanPhone });
    } catch (err) {
        console.error(`[Chat ${chatId}] Error in /pair:`, err);
        return res.status(500).json({ error: err.message });
    }
});

app.get('/status', async (req, res) => {
    const chatId = req.query.chat_id;
    if (!chatId) {
        return res.status(400).json({ error: "Missing chat_id parameter" });
    }
    const session = sessions[chatId];
    if (!session) {
        return res.json({ registered: false, connected: false });
    }
    const registered = session.sock && session.sock.authState && session.sock.authState.creds.registered;
    const connected = session.isConnected;
    return res.json({ registered: !!registered, connected: !!connected });
});

app.get('/check', async (req, res) => {
    const phone = req.query.phone;
    const chatId = req.query.chat_id;
    if (!phone) {
        return res.status(400).json({ error: "Missing phone parameter" });
    }
    if (!chatId) {
        return res.status(400).json({ error: "Missing chat_id parameter" });
    }

    const session = sessions[chatId];
    if (!session || !session.sock || !session.sock.authState || !session.sock.authState.creds.registered) {
        return res.status(503).json({ registered: null, error: "WhatsApp Checker is not linked or authenticated for your account yet. Use /pair command to link." });
    }

    if (!session.isConnected) {
        return res.status(503).json({ registered: null, error: "WhatsApp connection is currently closed/reconnecting. Please wait a few seconds and try again." });
    }

    try {
        const cleanPhone = phone.replace(/[^\d]/g, '');
        const jid = `${cleanPhone}@s.whatsapp.net`;

        console.log(`[Chat ${chatId}] Checking registration for: ${cleanPhone}`);
        let results = await session.sock.onWhatsApp(jid);

        if (!results || results.length === 0) {
            results = await session.sock.onWhatsApp(cleanPhone);
        }

        const registered = results && results.length > 0 && (results[0].exists || results[0].exists === undefined);
        console.log(`[Chat ${chatId}] Result for ${cleanPhone}: registered = ${!!registered}`);
        return res.json({ registered: !!registered });
    } catch (e) {
        console.error(`[Chat ${chatId}] Error in /check:`, e);
        return res.status(500).json({ registered: null, error: e.message });
    }
});

app.listen(port, () => {
    console.log(`User-Isolated WA Checker Server listening on port ${port}`);
});
