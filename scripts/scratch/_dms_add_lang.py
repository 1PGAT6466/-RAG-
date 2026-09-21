#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add 'preview_image' language key to zh_CN and en_GB lang.inc"""
import io

def add_key(path, value):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if "'preview_image'" in src:
        print("already present in", path)
        return False
    # insert before the first appearance of "'preview_pdf' =>" line's next line? simpler:
    # insert right before the final ");"
    idx = src.rstrip().rfind(");")
    if idx == -1:
        print("WARN no ');' in", path)
        return False
    insert = "'preview_image' => '%s',\n" % value
    new = src[:idx] + insert + src[idx:]
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print("added to", path)
    return True

add_key("/home/www-data/seeddms60x/seeddms-6.0.41/languages/zh_CN/lang.inc", "预览图")
add_key("/home/www-data/seeddms60x/seeddms-6.0.41/languages/en_GB/lang.inc", "Preview")
