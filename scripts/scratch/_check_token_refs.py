import os, re
root = r'E:\更新RAG框架\frontend\src'
pattern = re.compile(r"localStorage\.(get|set|remove)Item\(['\"]token['\"]\)")
for dirpath, _, files in os.walk(root):
    for f in files:
        if f.endswith(('.vue', '.js')):
            fp = os.path.join(dirpath, f)
            for i, line in enumerate(open(fp, encoding='utf-8'), 1):
                if pattern.search(line):
                    rel = os.path.relpath(fp, root)
                    print(f"{rel}:{i}: {line.strip()}")
