#!/bin/bash
# install-and-run.sh - Quick setup script for new features

echo "🚀 Citra AI - Feature Installation Script"
echo "=============================================="
echo ""

# Check if npm is installed
if ! command -v npm &> /dev/null
then
    echo "❌ Error: npm is not installed"
    echo "Please install Node.js and npm first"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
npm install

# Check if installation succeeded
if [ $? -eq 0 ]; then
    echo "✅ Dependencies installed successfully!"
    echo ""
    echo "🎉 Setup Complete!"
    echo ""
    echo "📋 New Features Available:"
    # Hidden: Project Management is hidden from the UI.
    # echo "  ✅ Enhanced Kanban Board (Project Management → Kanban Board tab)"
    echo "  ✅ Resource badges on task cards"
    echo "  ✅ Drag-and-drop task management"
    echo "  ✅ Over-allocation warnings"
    echo ""
    echo "🚀 To start the app, run:"
    echo "  npm start"
    echo ""
    echo "📚 Documentation:"
    echo "  - Quick Start: QUICK_START_GUIDE.md"
    echo "  - Features: IMPLEMENTATION_STATUS.md"
    echo "  - Chat Integration: CHAT_INTEGRATION_GUIDE.md"
    echo ""
else
    echo "❌ Installation failed"
    echo "Please check the error messages above"
    exit 1
fi
