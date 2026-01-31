#!/bin/bash
echo "=== PRE-DEPLOY CHECK ==="

echo ""
echo "1. Git status:"
cd /opt/aila && git status --short

echo ""
echo "2. Незакоммиченные изменения:"
git diff --stat | tail -5

echo ""
echo "3. Синтаксис Python:"
/opt/aila/venv/bin/python -c "import ast; ast.parse(open('/opt/aila/aila/api/routes/admin.py').read()); print('✓ admin.py OK')" 2>&1 || echo "✗ admin.py ERROR"

echo ""
echo "4. Бот запускается:"
timeout 5 /opt/aila/venv/bin/python -c "from aila.api.routes import admin; print('✓ Import OK')" 2>/dev/null || echo "✗ Import ERROR"

echo ""
echo "5. Последние 3 коммита:"
git log --oneline -3

echo ""
echo "=== CHECK COMPLETE ==="
