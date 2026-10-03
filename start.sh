#!/bin/bash
# AI Studio Environment: Port 3000 is required
export PORT=3000

# Add local bin to PATH for user-installed packages
export PATH=$HOME/.local/bin:$PATH

# Check if pip is available, install if missing
python3 -m pip --version &>/dev/null
if [ $? -ne 0 ]; then
    echo "📦 Pip not found. Downloading and installing..."
    curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip-install.py
    python3 get-pip-install.py --user
    rm get-pip-install.py
fi

# Install requirements
echo "📥 Installing dependencies from requirements.txt..."
python3 -m pip install -r requirements.txt --user

# Run the app
echo "🚀 Starting main.py..."
python3 main.py
