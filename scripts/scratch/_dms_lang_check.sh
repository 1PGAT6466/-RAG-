#!/bin/sh
echo "=== languages dir ==="
ls /home/www-data/seeddms60x/seeddms-6.0.41/languages/ | head -40
echo "=== settings language ==="
grep -n 'language' /home/www-data/seeddms60x/conf/settings.xml
echo "=== zh_CN file ==="
ls -la /home/www-data/seeddms60x/seeddms-6.0.41/languages/zh_CN/
echo "=== check admin lang pref ==="
grep -rn 'preview_pdf\|"preview"' /home/www-data/seeddms60x/seeddms-6.0.41/languages/zh_CN/*.php 2>/dev/null | head
