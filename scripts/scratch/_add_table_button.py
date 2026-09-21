path = "/home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap/class.ViewDocument.php"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# Insert a "table preview" button before the PDF preview block for spreadsheet mime types.
anchor = """			if($converttopdf && !$settings->extensionIsDisabled('pdfviewer')) {"""

new_block = """			// Spreadsheet table preview (xlsx/xls/csv/ods) - shows full data in an HTML table
			$sheetMimes = array(
				'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
				'application/vnd.ms-excel',
				'application/vnd.oasis.opendocument.spreadsheet',
				'text/csv',
			);
			if(in_array($latestContent->getMimeType(), $sheetMimes)) {
				$this->contentHeading('表格预览');
?>
				<div style="margin-bottom:8px">
					<a href="<?php echo $settings->_httpRoot; ?>op/op.TablePreview.php?documentid=<?php echo $latestContent->getDocument()->getID(); ?>&version=<?php echo $latestContent->getVersion(); ?>" target="_blank" rel="noopener" class="btn btn-primary btn-sm">&#128202; 在新页面查看完整表格数据</a>
				</div>
<?php
			}

""" + anchor

if anchor not in c:
    print("ERROR: anchor not found")
    raise SystemExit(1)

c = c.replace(anchor, new_block, 1)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("OK: table preview button added")
