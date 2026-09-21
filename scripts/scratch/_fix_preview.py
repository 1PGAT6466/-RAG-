path = r"E:\更新RAG框架\frontend\src\components\FilePreview.vue"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
old = """'预览：' + (fileName || '')"""
new = """(isDmsMode ? 'DMS 预览：' : '预览：') + (effectiveName || '')"""
c = c.replace(old, new)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("OK")
