#!/bin/bash
# Upload local database to Railway
# This requires an admin API endpoint on Railway (which we'll create)

set -e

DB_FILE="data/pilotai.db"
RAILWAY_URL="https://attix-production.up.railway.app"
UPLOAD_ENDPOINT="${RAILWAY_URL}/api/admin/upload-db"

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    echo "Error: Database file not found at $DB_FILE"
    exit 1
fi

echo "📦 Preparing to upload database to Railway..."
echo "   Local DB: $DB_FILE ($(du -h $DB_FILE | cut -f1))"
echo "   Railway URL: $RAILWAY_URL"
echo ""

# Get admin token from environment or prompt
if [ -z "$RAILWAY_ADMIN_TOKEN" ]; then
    echo "Please enter Railway admin token (or set RAILWAY_ADMIN_TOKEN env var):"
    read -s RAILWAY_ADMIN_TOKEN
fi

echo "🚀 Uploading database..."

# Upload via multipart form
RESPONSE=$(curl -X POST "$UPLOAD_ENDPOINT" \
  -H "Authorization: Bearer $RAILWAY_ADMIN_TOKEN" \
  -F "database=@$DB_FILE" \
  -w "\nHTTP_CODE:%{http_code}" \
  -s)

HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_CODE")

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Database uploaded successfully!"
    echo ""
    echo "Verifying on Railway..."
    
    # Verify the upload
    VERIFY=$(curl -s "${RAILWAY_URL}/api/paper-trades" | python3 -c "import sys, json; d=json.load(sys.stdin); print(f\"Trades: {d['stats']['total_trades']} | Balance: ${d['stats']['balance']}\")")
    echo "   $VERIFY"
    echo ""
    echo "🎉 Migration complete! Railway is now your primary instance."
else
    echo "❌ Upload failed (HTTP $HTTP_CODE)"
    echo "Response: $BODY"
    exit 1
fi
