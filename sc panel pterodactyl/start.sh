#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — Startup script for Pterodactyl Panel (Node.js only)
# ─────────────────────────────────────────────────────────────────────────────
# This script runs inside the Pterodactyl container and:
#   1. Restores encrypted WhatsApp sessions (if present)
#   2. Installs Node.js dependencies
#   3. Starts the WhatsApp Checker (Baileys) as the main process
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "========================================"
echo "  SC Panel Pterodactyl — Node.js"
echo "  WhatsApp Checker (Baileys)"
echo "========================================"
echo ""

# ── 1. Restore encrypted WhatsApp sessions (if present) ─────────────────────
if [ -f "data/baileys_auths.enc" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "[1/3] Restoring encrypted WhatsApp sessions..."
    openssl enc -d -aes-256-cbc -pbkdf2 \
        -in data/baileys_auths.enc \
        -out data/baileys_auths.zip \
        -pass pass:"$TELEGRAM_BOT_TOKEN" 2>/dev/null || true
    if [ -f "data/baileys_auths.zip" ]; then
        unzip -q -o data/baileys_auths.zip 2>/dev/null || true
        rm -f data/baileys_auths.zip
        echo "      Encrypted WhatsApp sessions restored."
    else
        echo "      No valid encrypted session found — starting fresh."
    fi
elif [ -f "data/baileys_auth.enc" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "[1/3] Restoring legacy encrypted WhatsApp session..."
    openssl enc -d -aes-256-cbc -pbkdf2 \
        -in data/baileys_auth.enc \
        -out data/baileys_auth.zip \
        -pass pass:"$TELEGRAM_BOT_TOKEN" 2>/dev/null || true
    if [ -f "data/baileys_auth.zip" ]; then
        unzip -q -o data/baileys_auth.zip 2>/dev/null || true
        rm -f data/baileys_auth.zip
        if [ -d "data/baileys_auth" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
            mv data/baileys_auth "data/baileys_auth_$TELEGRAM_CHAT_ID"
        fi
        echo "      Legacy WhatsApp session restored and migrated."
    else
        echo "      No valid legacy session found — starting fresh."
    fi
else
    echo "[1/3] No encrypted WhatsApp sessions found — starting fresh."
fi

echo ""

# ── 2. Install Node.js dependencies ──────────────────────────────────────────
echo "[2/3] Installing Node.js dependencies..."
npm install --production --no-audit --no-fund 2>&1 | tail -5
echo "      Node.js dependencies installed."
echo ""

# ── 3. Start WhatsApp Checker (main process) ─────────────────────────────────
echo "[3/3] Starting WhatsApp Checker (Baileys)..."
echo "========================================"
echo ""
exec node wa_checker.js
