#!/bin/bash
export TZ='Asia/Kolkata'
source /root/trading/venv/bin/activate
cd /root/trading

# Create date-based log directory (e.g., 20MAR2026)
LOG_DATE=$(date +%d%b%Y | tr '[:lower:]' '[:upper:]')
LOG_DIR="/root/trading/assets/logs/${LOG_DATE}"
mkdir -p "$LOG_DIR"

# Run login script with date-based log file
python login.py >> "${LOG_DIR}/login.log" 2>&1