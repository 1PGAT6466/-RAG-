content = open(r'E:\更新RAG框架\frontend\src\views\ChatView.vue', encoding='utf-8').read()
import re
for m in re.finditer(r'mode', content):
    start = max(0, m.start()-60)
    end = min(len(content), m.end()+60)
    line = content[:m.start()].count('\n')+1
    snippet = content[start:end].replace('\n', ' ')
    print(f'L{line}: ...{snippet}...')
