#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Use getMLText key 'preview_image' instead of raw Chinese label in the button."""
import io
P = "/home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap/class.ViewDocument.php"
with io.open(P, "r", encoding="utf-8") as f:
    src = f.read()
old = "'label'=>'预览图'"
new = "'label'=>'preview_image'"
if old in src:
    src = src.replace(old, new)
    print("replaced label -> preview_image")
elif new in src:
    print("already preview_image")
else:
    print("WARN label not found")
with io.open(P, "w", encoding="utf-8") as f:
    f.write(src)
