#!/bin/bash

# Newsletter wrapper script for cron execution
# Handles environment setup and error logging

# Set up environment
export PATH="/home/mywang/.local/bin:/usr/local/bin:/usr/bin:/bin"
export HOME="/home/mywang"

# Change to project directory
cd /home/mywang/Code/kestral || {
    echo "Failed to cd to project directory" >> /tmp/newsletter_cron_errors.log
    exit 1
}

# Activate virtual environment
source .venv/bin/activate || {
    echo "Failed to activate virtual environment" >> /tmp/newsletter_cron_errors.log
    exit 1
}

# Run the newsletter with full error capture
python main.py --platform=twitter --send --to wmichael.cs@gmail.com --window 50  2>&1 | tee -a /tmp/newsletter_cron.log

# Log completion
echo "Newsletter run completed at $(date)" >> /tmp/newsletter_cron.log
