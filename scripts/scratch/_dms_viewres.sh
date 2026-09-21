#!/bin/sh
echo "=== bootstrap4 dir contents ==="
ls /home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap4/ | head -50
echo "=== how views are loaded ==="
grep -rn "views/'\|views/\"\|getThemeDir\|_viewDir\|require.*View" /home/www-data/seeddms60x/seeddms-6.0.41/inc/inc.ClassViewCommon.php 2>/dev/null | head -20
echo "=== seeddms view dir resolution ==="
grep -rn "theme" /home/www-data/seeddms60x/seeddms-6.0.41/inc/inc.ClassViewCommon.php 2>/dev/null | head -30
echo "=== out/index.php view require ==="
grep -rn "views" /home/www-data/seeddms60x/seeddms-6.0.41/out/out.ViewDocument.php 2>/dev/null | head
