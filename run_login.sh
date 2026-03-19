#!/bin/bash
export TZ='Asia/Kolkata'
source /root/trading/venv/bin/activate
cd /root/trading
python login.py >> /root/trading/login.log 2>&1