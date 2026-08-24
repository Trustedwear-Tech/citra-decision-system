<!--
  Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
  Author: Rohit Kumar Chandan
  SPDX-License-Identifier: Apache-2.0

  Licensed under the Apache License, Version 2.0 (the "License"); you may not
  use this file except in compliance with the License. You may obtain a copy of
  the License at http://www.apache.org/licenses/LICENSE-2.0
-->

# Install system dependencies
sudo apt-get update
sudo apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2

# Then install Playwright browsers
cd playwright-render-service
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium  # Installs missing system deps

# Run
python main.py



# Windows (your current setup)
python main.py

# Linux/Ubuntu production
uvicorn main:app --host 0.0.0.0 --port 3001 --workers 2