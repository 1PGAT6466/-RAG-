#!/bin/sh
# Login and fetch document 103 detail page, extract the actions row
C="mydms_session"
rm -f /tmp/ck.txt /tmp/doc103.html
curl -s -c /tmp/ck.txt -b /tmp/ck.txt -o /dev/null \
  -d "login=admin&pwd=admin" \
  "http://localhost/op/op.Login.php"
curl -s -b /tmp/ck.txt -c /tmp/ck.txt -o /tmp/doc103.html \
  "http://localhost/out/out.ViewDocument.php?documentid=103"
echo "=== bytes ==="
wc -c /tmp/doc103.html
echo "=== action-list ul (first 3) ==="
grep -o 'action-list[^>]*>.*</ul>' /tmp/doc103.html | head -c 2000
echo ""
echo "=== preview_image button present? ==="
grep -o 'op.TablePreview.php[^"]*' /tmp/doc103.html | head
grep -c '预览图' /tmp/doc103.html
echo "=== any iframe (should be 0 for preview) ==="
grep -c '<iframe' /tmp/doc103.html
