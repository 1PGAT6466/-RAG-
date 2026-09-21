#!/bin/sh
echo "=== views dirs ==="
ls -d /home/www-data/seeddms60x/seeddms-6.0.41/views/*/
echo "=== bootstrap4 ViewDocument exists? ==="
ls -la /home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap4/class.ViewDocument.php 2>/dev/null
echo "=== grep preview blocks in bootstrap4 ==="
grep -n 'TablePreview\|在新页面\|contentHeading(getMLText("preview")\|converttopdf\|label.*download' /home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap4/class.ViewDocument.php 2>/dev/null | head -40
echo "=== check theme override ==="
grep -rn 'theme' /home/www-data/seeddms60x/conf/settings.xml | head
