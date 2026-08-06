#!/bin/bash
echo "=== Starting Pterodactyl Deployment Script ==="

# 1. Load environment variables from .env if it exists
if [ -f .env ]; then
    echo "Loading environment variables from .env..."
    # Export vars without comments
    export $(grep -v '^#' .env | xargs)
fi

# 2. Setup virtual environment for Python to prevent externally-managed environment errors
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# 3. Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# 4. Install/upgrade pip and dependencies
echo "Installing/Updating Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Install Node.js dependencies
echo "Installing Node.js dependencies..."
npm install @whiskeysockets/baileys express qrcode-terminal pino dotenv --no-audit --no-fund

# 6. Kill existing processes on port 3000 to prevent port-in-use errors
echo "Cleaning up existing processes on port 3000..."
kill $(lsof -t -i :3000) 2>/dev/null || true

# 7. Start wa_checker.js in background
echo "Starting WhatsApp Checker (Node.js) in the background..."
node wa_checker.js > wa_checker.log 2>&1 &

# 8. Start Python Telegram Bot
echo "Starting Python Telegram Bot..."
python bot/main.py
