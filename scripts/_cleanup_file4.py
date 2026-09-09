import sys
sys.path.insert(0, '.')
from src.storage.db import _get_conn, delete_file

c = _get_conn()
f = c.execute('SELECT id, name FROM files WHERE id=4').fetchone()
print('待清理文件:', dict(f) if f else None)

if f:
    delete_file(4)
    print('delete_file(4) 完成')

# 标记所有 running 任务为 failed（服务已停，任务中断）
n = c.execute("UPDATE tasks SET status='failed', error='服务重启，任务中断' WHERE status='running'").rowcount
c.commit()
print('标记 failed 的 running 任务数:', n)

print('--- 清理后 ---')
print('files count:', c.execute('SELECT COUNT(*) FROM files').fetchone()[0])
print('chunks count:', c.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])
print('file 4 是否存在:', c.execute('SELECT COUNT(*) FROM files WHERE id=4').fetchone()[0])
print('file 4 chunks:', c.execute('SELECT COUNT(*) FROM chunks WHERE file_id=4').fetchone()[0])
