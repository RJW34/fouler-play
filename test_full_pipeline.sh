#!/bin/bash
set -e

echo "====================================="
echo "Fouler Play Pipeline Integration Test"
echo "====================================="

cd /home/ryan/projects/fouler-play
source venv/bin/activate

echo ""
echo "✅ Virtual environment activated"

echo ""
echo "📊 Checking battle stats..."
python -c "
import json
data = json.load(open('battle_stats.json'))
print(f'Total battles recorded: {len(data[\"battles\"])}')
"

echo ""
echo "🔌 Testing MAGNETON connectivity..."
ssh "${MAGNETON_HOST:-Ryanj@jigglypuff.tail4859dd.ts.net}" "ollama --version" || {
    echo "❌ MAGNETON connection failed"
    exit 1
}

echo ""
echo "🤖 Testing Ollama API..."
echo '{"model":"qwen2.5-coder:7b","prompt":"Hello","stream":false}' | \
    ssh "${MAGNETON_HOST:-Ryanj@jigglypuff.tail4859dd.ts.net}" "curl -s -X POST http://localhost:11434/api/generate -H 'Content-Type: application/json' -d @-" | \
    grep -q "response" && echo "✅ Ollama API responding" || {
    echo "❌ Ollama API test failed"
    exit 1
}

echo ""
echo "📝 Generating test report..."
python generate_test_report.py || {
    echo "❌ Report generation failed"
    exit 1
}

echo ""
echo "📤 Testing Discord notification..."
python -c "
from pathlib import Path
from pipeline import Pipeline
import sys

pipeline = Pipeline()
report = pipeline.analyzer.get_latest_report()
if not report:
    print('❌ No report found')
    sys.exit(1)

print(f'Latest report: {report.name}')
pipeline.send_discord_notification(report)
"

echo ""
echo "====================================="
echo "✅ All pipeline tests passed!"
echo "====================================="
echo ""
echo "📖 Next steps:"
echo "  1. Run manually: python pipeline.py analyze"
echo "  2. View report: python pipeline.py report"
echo "  3. Start watcher: python pipeline.py watch"
echo "  4. Install service: sudo cp fouler-pipeline.service /etc/systemd/system/"
echo ""
