#!/bin/sh
D=/home/www-data/seeddms60x/seeddms-6.0.41
echo "=== 预览图 label key in ViewDocument ==="
grep -c "label'=>'preview_image'" $D/views/bootstrap/class.ViewDocument.php
echo "=== no iframe in preview() (count of '<iframe' in whole file) ==="
grep -c '<iframe' $D/views/bootstrap/class.ViewDocument.php
echo "=== lang key zh_CN ==="
grep -c "preview_image" $D/languages/zh_CN/lang.inc
echo "=== lang key en_GB ==="
grep -c "preview_image" $D/languages/en_GB/lang.inc
echo "=== TablePreview exists ==="
test -f $D/op/op.TablePreview.php && echo OK
echo "=== php -l ==="
php -l $D/views/bootstrap/class.ViewDocument.php
