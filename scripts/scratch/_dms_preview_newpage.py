import re

path = "/home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap/class.ViewDocument.php"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old_block = """			if($converttopdf && !$settings->extensionIsDisabled('pdfviewer')) {
				$pdfpreviewer = new SeedDMS_Preview_PdfPreviewer($cachedir, $timeout, $xsendfile);
				if($conversionmgr)
					$pdfpreviewer->setConversionMgr($conversionmgr);
				else
					$pdfpreviewer->setConverters($pdfconverters);
				if($pdfpreviewer->hasConverter($latestContent->getMimeType())) {
					$this->contentHeading(getMLText("preview_pdf"));
?>
				<div style="width:100%; height: 0; position:relative; padding-top: 141%;">
				<iframe src="<?= $settings->_httpRoot ?>ext/pdfviewer/res/web/viewer.html?file=<?php echo urlencode($settings->getBaseUrlWithRoot().'op/op.PdfPreview.php?documentid='.$latestContent->getDocument()->getID().'&version='.$latestContent->getVersion()); ?>" _width="100%" _height="700px" style="position: absolute; top: 0; left: 0; bottom: 0; right: 0; width:    100%; height: 100%"></iframe>
				</div>
<?php
				}
			}"""

new_block = """			if($converttopdf && !$settings->extensionIsDisabled('pdfviewer')) {
				$pdfpreviewer = new SeedDMS_Preview_PdfPreviewer($cachedir, $timeout, $xsendfile);
				if($conversionmgr)
					$pdfpreviewer->setConversionMgr($conversionmgr);
				else
					$pdfpreviewer->setConverters($pdfconverters);
				if($pdfpreviewer->hasConverter($latestContent->getMimeType())) {
					$viewerUrl = $settings->_httpRoot.'ext/pdfviewer/res/web/viewer.html?file='.urlencode($settings->getBaseUrlWithRoot().'op/op.PdfPreview.php?documentid='.$latestContent->getDocument()->getID().'&version='.$latestContent->getVersion());
					$directUrl = $settings->getBaseUrlWithRoot().'op/op.PdfPreview.php?documentid='.$latestContent->getDocument()->getID().'&version='.$latestContent->getVersion();
					$this->contentHeading(getMLText("preview_pdf"));
?>
				<div style="margin-bottom:8px">
					<a href="<?php echo htmlspecialchars($viewerUrl); ?>" target="_blank" rel="noopener" class="btn btn-primary btn-sm">&#128196; <?php echo getMLText("preview") ? getMLText("preview") : 'Preview'; ?> — <?php echo getMLText("preview_pdf"); ?></a>
					<a href="<?php echo htmlspecialchars($directUrl); ?>" target="_blank" rel="noopener" class="btn btn-secondary btn-sm">&#128065; 在新页面打开 PDF</a>
				</div>
				<div style="width:100%; height: 0; position:relative; padding-top: 141%;">
				<iframe src="<?php echo htmlspecialchars($viewerUrl); ?>" _width="100%" _height="700px" style="position: absolute; top: 0; left: 0; bottom: 0; right: 0; width:    100%; height: 100%"></iframe>
				</div>
<?php
				}
			}"""

if old_block not in c:
    print("ERROR: old block not found exactly")
    # Fallback: search for iframe
    idx = c.find('ext/pdfviewer/res/web/viewer.html')
    if idx > 0:
        print("Found viewer reference at", idx)
        print(repr(c[idx-400:idx+200]))
    raise SystemExit(1)

c = c.replace(old_block, new_block)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("OK: preview block updated with new-page buttons")
