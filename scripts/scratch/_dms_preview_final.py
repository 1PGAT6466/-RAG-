#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SeedDMS ViewDocument.php final preview rework:
1) Remove the embedded preview area entirely (no iframe/table preview rendering before click).
2) Keep a single "预览图" button, placed next to the Download button (same actions row).
3) Button opens a new browser tab (PDF via pdf.js; spreadsheets via TablePreview HTML page).
"""
import io, sys, re

PATH = "/home/www-data/seeddms60x/seeddms-6.0.41/views/bootstrap/class.ViewDocument.php"

with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

orig = src

# ---------------------------------------------------------------
# 1) Remove the whole embedded rendering inside preview():
#    everything from "$txt = $this->callHook('documentPreview'..." else-branch
#    down to the end of the spreadsheet + converttopdf blocks.
#    Simpler: we surgically remove three big chunks.
# ---------------------------------------------------------------

# Chunk A: spreadsheet table preview block
chunkA = """
			// Spreadsheet table preview (xlsx/xls/csv/ods) - shows full data in an HTML table
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
"""
if chunkA in src:
    src = src.replace(chunkA, "\n")
    print("chunkA (table preview block) removed")
else:
    print("WARN chunkA not found")

# Chunk B: converttopdf block
chunkB = """			if($converttopdf && !$settings->extensionIsDisabled('pdfviewer')) {
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
					<a href="<?php echo htmlspecialchars($viewerUrl); ?>" target="_blank" rel="noopener" class="btn btn-primary btn-sm">&#128196; 在新页面打开预览</a>
				</div>
				<div style="width:100%; height: 0; position:relative; padding-top: 141%;">
				<iframe src="<?php echo htmlspecialchars($viewerUrl); ?>" _width="100%" _height="700px" style="position: absolute; top: 0; left: 0; bottom: 0; right: 0; width:    100%; height: 100%"></iframe>
				</div>
<?php
				}
			}
"""
if chunkB in src:
    src = src.replace(chunkB, "\n")
    print("chunkB (converttopdf block) removed")
else:
    print("WARN chunkB not found")

# Chunk C: pdf mime case (embedded iframe) -> remove whole case block
chunkC = """				case 'application/pdf':
					if (!$settings->extensionIsDisabled('pdfviewer')) {
					$this->contentHeading(getMLText("preview"));
?>
			<div style="width:100%; height: 0; position:relative; padding-top: 141%; min-height: 800px;">
			<iframe src="<?= $settings->_httpRoot ?>ext/pdfviewer/res/web/viewer.html?file=<?php echo urlencode($settings->getBaseUrlWithRoot().'op/op.ViewOnline.php?documentid='.$latestContent->getDocument()->getID().'&version='.$latestContent->getVersion()); ?>" _width="100%" _height="100%" style="position: absolute; top: 0; left: 0; bottom: 0; right: 0; width: 100%; height: 100%"></iframe>
			</div>
<?php
					}
					break;
"""
if chunkC in src:
    src = src.replace(chunkC, """				case 'application/pdf':
					// preview opened in a new tab via the "预览图" action button
					break;
""")
    print("chunkC (pdf iframe case) replaced with no-op")
else:
    print("WARN chunkC not found")

# Chunk D: audio/video/image cases also embed content -> keep minimal, remove headings/embeds
# audio
chunkD = """				case 'audio/mpeg':
				case 'audio/mp3':
				case 'audio/ogg':
				case 'audio/wav':
					$this->contentHeading(getMLText("preview"));
?>
		<audio controls style="width: 100%;" preload="false">
		<source  src="<?= $settings->_httpRoot ?>op/op.ViewOnline.php?documentid=<?php echo $latestContent->getDocument()->getID(); ?>&version=<?php echo $latestContent->getVersion(); ?>" type="audio/mpeg">
		</audio>
<?php
					break;
"""
chunkD_new = """				case 'audio/mpeg':
				case 'audio/mp3':
				case 'audio/ogg':
				case 'audio/wav':
					// preview opened in a new tab via the "预览图" action button
					break;
"""
if chunkD in src:
    src = src.replace(chunkD, chunkD_new)
    print("chunkD (audio) replaced")
else:
    print("WARN chunkD not found")

# video
chunkE = """				case 'video/webm':
				case 'video/mp4':
				case 'video/mpeg':
				case 'video/avi':
				case 'video/msvideo':
				case 'video/x-msvideo':
				case 'video/x-matroska':
					$this->contentHeading(getMLText("preview"));
?>
			<video controls style="width: 100%;">
			<source  src="<?= $settings->_httpRoot ?>op/op.ViewOnline.php?documentid=<?php echo $latestContent->getDocument()->getID(); ?>&version=<?php echo $latestContent->getVersion(); ?>" type="video/mp4">
			</video>
<?php
					break;
"""
chunkE_new = """				case 'video/webm':
				case 'video/mp4':
				case 'video/mpeg':
				case 'video/avi':
				case 'video/msvideo':
				case 'video/x-msvideo':
				case 'video/x-matroska':
					// preview opened in a new tab via the "预览图" action button
					break;
"""
if chunkE in src:
    src = src.replace(chunkE, chunkE_new)
    print("chunkE (video) replaced")
else:
    print("WARN chunkE not found")

# image
chunkF = """				case 'image/svg+xml':
				case 'image/jpg':
				case 'image/jpeg':
				case 'image/png':
				case 'image/gif':
					$this->contentHeading(getMLText("preview"));
?>
			<img src="<?= $settings->_httpRoot ?>op/op.ViewOnline.php?documentid=<?php echo $latestContent->getDocument()->getID(); ?>&version=<?php echo $latestContent->getVersion(); ?>" width="100%">
<?php
					break;
"""
chunkF_new = """				case 'image/svg+xml':
				case 'image/jpg':
				case 'image/jpeg':
				case 'image/png':
				case 'image/gif':
					// preview opened in a new tab via the "预览图" action button
					break;
"""
if chunkF in src:
    src = src.replace(chunkF, chunkF_new)
    print("chunkF (image) replaced")
else:
    print("WARN chunkF not found")

# ---------------------------------------------------------------
# 2) Add a single "预览图" action button next to Download.
#    Insert into the first $items array in documentInfos() (the row with download).
# ---------------------------------------------------------------

anchor = """			if($accessobject->check_controller_access('Download', array('action'=>'version')))
				$items[] = array('link'=>$this->params['settings']->_httpRoot."op/op.Download.php?documentid=".$latestContent->getDocument()->getId()."&version=".$latestContent->getVersion(), 'icon'=>'download', 'label'=>'download');
			if($accessobject->check_controller_access('ViewOnline', array('action'=>'run')))
				if ($viewonlinefiletypes && (in_array(strtolower($latestContent->getFileType()), $viewonlinefiletypes) || in_array(strtolower($latestContent->getMimeType()), $viewonlinefiletypes)))
					$items[] = array('link'=>$this->params['settings']->_httpRoot."op/op.ViewOnline.php?documentid=".$latestContent->getDocument()->getId()."&version=". $latestContent->getVersion(), 'icon'=>'eye', 'label'=>'view_online', 'target'=>'_blank');
"""

insert_block = """			if($accessobject->check_controller_access('Download', array('action'=>'version')))
				$items[] = array('link'=>$this->params['settings']->_httpRoot."op/op.Download.php?documentid=".$latestContent->getDocument()->getId()."&version=".$latestContent->getVersion(), 'icon'=>'download', 'label'=>'download');
			// ---- 预览图 button: opens preview in a new tab ----
			$previewMimes = array(
				'application/pdf',
				'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
				'application/vnd.ms-excel',
				'application/vnd.oasis.opendocument.spreadsheet',
				'text/csv',
				'application/msword',
				'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
				'application/vnd.openxmlformats-officedocument.presentationml.presentation',
				'application/vnd.ms-powerpoint',
			);
			$currentMime = $latestContent->getMimeType();
			if(in_array($currentMime, array('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.ms-excel','application/vnd.oasis.opendocument.spreadsheet','text/csv'))) {
				$previewLink = $this->params['settings']->_httpRoot."op/op.TablePreview.php?documentid=".$latestContent->getDocument()->getId()."&version=".$latestContent->getVersion();
			} else {
				$previewLink = $this->params['settings']->_httpRoot.'ext/pdfviewer/res/web/viewer.html?file='.urlencode($this->params['settings']->getBaseUrlWithRoot().'op/op.PdfPreview.php?documentid='.$latestContent->getDocument()->getId().'&version='.$latestContent->getVersion());
			}
			if($converttopdf || in_array($currentMime, array('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.ms-excel','application/vnd.oasis.opendocument.spreadsheet','text/csv'))) {
				$items[] = array('link'=>$previewLink, 'icon'=>'eye', 'label'=>'预览图', 'target'=>'_blank');
			}
			if($accessobject->check_controller_access('ViewOnline', array('action'=>'run')))
				if ($viewonlinefiletypes && (in_array(strtolower($latestContent->getFileType()), $viewonlinefiletypes) || in_array(strtolower($latestContent->getMimeType()), $viewonlinefiletypes)))
					$items[] = array('link'=>$this->params['settings']->_httpRoot."op/op.ViewOnline.php?documentid=".$latestContent->getDocument()->getId()."&version=". $latestContent->getVersion(), 'icon'=>'eye', 'label'=>'view_online', 'target'=>'_blank');
"""

if anchor in src:
    src = src.replace(anchor, insert_block)
    print("inserted 预览图 button next to download")
else:
    print("WARN anchor for download-row not found")

if src == orig:
    print("NO CHANGES MADE")
    sys.exit(1)

with io.open(PATH, "w", encoding="utf-8") as f:
    f.write(src)
print("WRITTEN OK")
