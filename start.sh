#!/bin/bash
# HelpDesk Pro - Startskript
set -e

echo "============================================"
echo "  HelpDesk Pro - Ticket & Asset System"
echo "============================================"

# Install dependencies if needed
if ! python -c "import flask" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements.txt -q
fi

# Initialize database and start app
python app.py
