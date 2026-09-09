"""补抽规则实体：修复中文材料词 \\b bug 导致的实体-chunk 关联缺失

背景：extract_rule 里材料匹配用 \\b 边界，对中文材料词（铜合金/黄铜/不锈钢等）永远
匹配不到，导致这些实体只被 LLM 抽取（仅覆盖部分 chunk），关联严重缺失。
修复 _match_word 后重新跑规则抽取，补齐 entity_chunks 关联（不动 embedding）。

用法：
    python scripts/backfill_rule_entities.py            # 补抽（幂等）
    python scripts/backfill_rule_entities.py --dry-run  # 只统计不写
"""
import sys
sys.path.insert(0, '.')
from src.storage import db
from src.extraction import entity_extractor

def main():
    dry = '--dry-run' in sys.argv
    conn = db._get_conn()

    chunks = conn.execute("SELECT id, file_id, content FROM chunks").fetchall()
    print(f'共 {len(chunks)} 个 chunk')

    total_new = 0
    total_entities = 0
    for c in chunks:
        entities = entity_extractor.extract_rule(c["content"])
        total_entities += len(entities)
        for ent in entities:
            # 找/建实体
            eid = db.upsert_entity(
                name=ent["name"], etype=ent["type"],
                aliases=ent.get("aliases", []),
                description=ent.get("description", ""),
                attributes=ent.get("attributes", {}),
            )
            if dry:
                continue
            # 关联 chunk（幂等：关联不存在才插入，避免重复累加 mention_count）
            conn.execute(
                "INSERT OR IGNORE INTO entity_chunks (entity_id, chunk_id, mention_count) VALUES (?,?,1)",
                (eid, c["id"]),
            )
            conn.execute(
                "INSERT OR IGNORE INTO entity_files (entity_id, file_id, mention_count) VALUES (?,?,1)",
                (eid, c["file_id"]),
            )
            total_new += 1
        conn.commit()

    if dry:
        print(f'[dry-run] 预计抽取 {total_entities} 个实体实例（含重复 upsert）')
        return

    print(f'补抽完成：处理 {len(chunks)} chunk，实体实例 {total_entities}')

    # 统计中文材料关联改善
    print()
    print('=== 补抽后中文材料 chunk 关联数 ===')
    for m in ['铜合金','黄铜','不锈钢','陶瓷','磷青铜']:
        in_content = conn.execute("SELECT COUNT(*) FROM chunks WHERE content LIKE ?", (f'%{m}%',)).fetchone()[0]
        linked = conn.execute("""
            SELECT COUNT(DISTINCT ec.chunk_id) FROM entity_chunks ec
            JOIN entities e ON e.id=ec.entity_id
            WHERE e.type='material' AND e.name=?
        """, (m,)).fetchone()[0]
        print(f'  {m}: 内容含词 {in_content} chunk → 已关联 {linked} chunk')

if __name__ == '__main__':
    main()
