#!/bin/sh
F=/home/www-data/seeddms60x/seeddms-6.0.41/languages/zh_CN/lang.inc
grep -n "preview' =>\|preview_pdf' =>\|view_online' =>\|'preview'" $F | head -20
echo "=== tail of lang.inc (last 5 lines) ==="
tail -5 $F
echo "=== en_GB preview ==="
grep -n "preview' =>\|preview_pdf' =>\|view_online' =>" /home/www-data/seeddms60x/seeddms-6.0.41/languages/en_GB/lang.inc | head
