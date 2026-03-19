#!/bin/bash
source /root/trading/venv/bin/activate
cd /root/trading
python nifty_option_streamer.py >> /root/trading/streamer.log 2>&1