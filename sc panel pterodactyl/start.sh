#!/bin/bash
# SMTP Generator Bot — Pterodactyl startup script
set -e

echo "Starting SMTP Generator Bot on Pterodactyl..."

if [ -f "data/baileys_auths.enc" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Restoring encrypted WhatsApp sessions..."
  openssl enc -d -aes-256-cbc -pbkdf2 -in data/baileys_auths.enc -out data/baileys_auths.zip -pass pass:"$TELEGRAM_BOT_TOKEN" 2>/dev/null || true
  if [ -f data/baileys_auths.zip ]; then
    unzip -q -o data/baileys_auths.zip 2>/dev/null || true
    rm -f data/baileys_auths.zip
  fi
elif [ -f "data/baileys_auth.enc" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
  echo "Restoring legacy encrypted WhatsApp session..."
  openssl enc -d -aes-256-cbc -pbkdf2 -in data/baileys_auth.enc -out data/baileys_auth.zip -pass pass:"$TELEGRAM_BOT_TOKEN" 2>/dev/null || true
  if [ -f data/baileys_auth.zip ]; then
    unzip -q -o data/baileys_auth.zip 2>/dev/null || true
    rm -f data/baileys_auth.zip
    if [ -d data/baileys_auth ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
      mv data/baileys_auth "data/baileys_auth_$TELEGRAM_CHAT_ID"
    fi
  fi
fi

npm install --production --no-audit --no-fund
node wa_checker.js > wa_checker.log 2>&1 &
pip install --no-cache-dir -r requirements.txt
cd bot
exec python main.py
