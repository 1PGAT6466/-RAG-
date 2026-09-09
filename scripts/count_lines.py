import os
total = 0
files = []
for root, dirs, fnames in os.walk(os.path.join(os.path.dirname(__file__), '..', 'src')):
    for f in fnames:
        if f.endswith('.py') and '__pycache__' not in root:
            fp = os.path.join(root, f)
            lines = sum(1 for _ in open(fp, encoding='utf-8'))
            total += lines
            rel = os.path.relpath(fp, os.path.join(os.path.dirname(__file__), '..'))
            files.append((lines, rel))
files.sort(reverse=True)
for lines, rel in files:
    print(f"{lines:4d}  {rel}")
print(f"\n总计: {total} 行, {len(files)} 个文件")
