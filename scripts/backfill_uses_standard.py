"""补跑 uses_standard 边（材料→标准，领域过滤版）

用法：
    python scripts/backfill_uses_standard.py            # 补跑（幂等）
    python scripts/backfill_uses_standard.py --dry-run  # 只预览不改
"""
import sys
sys.path.insert(0, '.')
import sqlite3
from config import DB_PATH
from src.storage import db
from src.extraction.relation_builder import _build_uses_standard_edges

def main():
    dry = '--dry-run' in sys.argv
    conn = db._get_conn()

    before = conn.execute(
        "SELECT COUNT(*) FROM entity_relations WHERE rel_type='uses_standard'").fetchone()[0]
    print(f'uses_standard 边（补跑前）: {before}')

    if dry:
        # 只预览候选
        candidates = conn.execute("""
            SELECT DISTINCT a.name AS mname, b.name AS sname
            FROM entity_chunks ec
            JOIN entities a ON a.id = ec.entity_id AND a.type='material'
            JOIN entity_chunks ec2 ON ec2.chunk_id = ec.chunk_id
            JOIN entities b ON b.id = ec2.entity_id AND b.type='standard'
            WHERE a.id != b.id AND json_extract(b.attributes, '$.standard_domain') = '材料'
            ORDER BY a.name, b.name
        """).fetchall()
        print(f'候选材料→材料标准 对: {len(candidates)}')
        for r in candidates:
            print(f'  {r[0]} -> {r[1]}')
        return

    n = _build_uses_standard_edges()
    after = conn.execute(
        "SELECT COUNT(*) FROM entity_relations WHERE rel_type='uses_standard'").fetchone()[0]
    print(f'新建 {n} 条 uses_standard 边')
    print(f'补跑后 uses_standard 边总数: {after}')

if __name__ == '__main__':
    main()
