path = "/home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap/class.ViewDocument.php"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old_block = """				<div style="margin-bottom:8px">
					<a href="<?php echo htmlspecialchars($viewerUrl); ?>" target="_blank" rel="noopener" class="btn btn-primary btn-sm">&#128196; <?php echo getMLText("preview") ? getMLText("preview") : 'Preview'; ?> — <?php echo getMLText("preview_pdf"); ?></a>
					<a href="<?php echo htmlspecialchars($directUrl); ?>" target="_blank" rel="noopener" class="btn btn-secondary btn-sm">&#128065; 在新页面打开 PDF</a>
				</div>"""

new_block = """				<div style="margin-bottom:8px">
					<a href="<?php echo htmlspecialchars($viewerUrl); ?>" target="_blank" rel="noopener" class="btn btn-primary btn-sm">&#128196; 在新页面打开预览</a>
				</div>"""

if old_block not in c:
    print("ERROR: block not found")
    raise SystemExit(1)

c = c.replace(old_block, new_block)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("OK: reduced to single button")
