<?php
//    TablePreview - render spreadsheet (xlsx/xls/csv) documents as an HTML table
//    for SeedDMS. Solves the "Excel -> PDF = 1080 pages / incomplete" preview problem.
//    Supports server-side pagination so arbitrarily large sheets are fully viewable.

include("../inc/inc.Settings.php");
include("../inc/inc.Utils.php");
include("../inc/inc.LogInit.php");
include("../inc/inc.Language.php");
include("../inc/inc.Init.php");
include("../inc/inc.Extension.php");
include("../inc/inc.DBInit.php");
include("../inc/inc.ClassUI.php");
include("../inc/inc.Authentication.php");

$documentid = isset($_GET["documentid"]) ? $_GET["documentid"] : 0;
if (!is_numeric($documentid) || intval($documentid) < 1) {
	header('HTTP/1.1 400 Bad Request');
	exit;
}

$document = $dms->getDocument($documentid);
if (!is_object($document)) {
	header('HTTP/1.1 404 Not Found');
	exit;
}

if ($document->getAccessMode($user) < M_READ) {
	header('HTTP/1.1 403 Forbidden');
	exit;
}

$version = null;
if (isset($_GET['version']) && is_numeric($_GET['version'])) {
	$version = intval($_GET['version']);
	$object = $document->getContentByVersion($version);
} else {
	$object = $document->getLatestContent();
}

if (!is_object($object)) {
	header('HTTP/1.1 404 Not Found');
	exit;
}

$orgname = $object->getOriginalFileName();
$docname = $document->getName();
$objversion = $object->getVersion();

// pagination
$PAGE_SIZE = 1000;
$page = isset($_GET['page']) && is_numeric($_GET['page']) ? max(1, intval($_GET['page'])) : 1;

// Fetch raw content
$tmpfile = tempnam(sys_get_temp_dir(), 'tablepreview-');
$content = $object->content();
if ($content === false || $content === null) {
	$path = $dms->contentDir . $object->getPath();
	$content = @file_get_contents($path);
}
file_put_contents($tmpfile, $content);

// Parse
$sheets = array(); // [ ['title'=>, 'rows'=>[[...]], 'total'=>int] ]
$parseError = '';
try {
	require_once '/home/www-data/seeddms60x/vendor/autoload.php';
	$reader = \PhpOffice\PhpSpreadsheet\IOFactory::createReaderForFile($tmpfile);
	$reader->setReadDataOnly(true);
	$spreadsheet = $reader->load($tmpfile);
	foreach ($spreadsheet->getWorksheetIterator() as $sheet) {
		$highestRow = $sheet->getHighestDataRow();
		$highestCol = $sheet->getHighestDataColumn();
		$colIdx = \PhpOffice\PhpSpreadsheet\Cell\Coordinate::columnIndexFromString($highestCol);
		$sheets[] = array(
			'title' => $sheet->getTitle(),
			'rows' => $sheet->rangeToArray('A1:' . $highestCol . $highestRow, '', true, false, false),
			'total' => $highestRow,
			'cols' => $colIdx,
		);
	}
} catch (\Throwable $e) {
	$parseError = $e->getMessage();
}
@unlink($tmpfile);

// For pagination we apply the page only to the FIRST sheet (typical case: single sheet).
// All sheets are shown, but pagination slices rows within each sheet.
?>
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title><?php echo htmlspecialchars($orgname); ?> - 表格预览</title>
<style>
  body { margin:0; font:13px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif; background:#f5f6f8; color:#222; }
  .hd { position:sticky; top:0; z-index:20; background:#2a2a2a; color:#ddd; padding:10px 16px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .hd .name { font-weight:600; color:#fff; max-width:40vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .hd .meta { color:#aaa; font-size:12px; }
  .hd .spacer { flex:1; }
  .hd a { color:#7db8ff; text-decoration:none; }
  .hd a:hover { text-decoration:underline; }
  .pager { padding:8px 16px; background:#fff; border-bottom:1px solid #d5d9dd; display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:12px; }
  .pager a, .pager span.cur { padding:3px 10px; border:1px solid #ccc; border-radius:4px; text-decoration:none; color:#333; }
  .pager a:hover { background:#eef1f4; }
  .pager span.cur { background:#2a6cf0; color:#fff; border-color:#2a6cf0; }
  .sheet-title { padding:10px 16px; font-weight:700; background:#e9ecef; border-bottom:1px solid #d5d9dd; }
  table.tbl { border-collapse:collapse; background:#fff; width:max-content; min-width:100%; }
  table.tbl td { border:1px solid #dcdfe3; padding:5px 10px; white-space:nowrap; max-width:420px; overflow:hidden; text-overflow:ellipsis; }
  table.tbl tr:first-child td { background:#eef1f4; font-weight:600; position:sticky; top:0; }
  table.tbl tr:nth-child(even) td { background:#fafbfc; }
  .trunc { padding:10px 16px; background:#fff3cd; border-top:1px solid #ffe08a; color:#7a5b00; }
  .err { padding:24px; color:#c0392b; }
</style>
</head>
<body>
<div class="hd">
  <span class="name"><?php echo htmlspecialchars($orgname); ?></span>
  <span class="meta">v<?php echo $objversion; ?></span>
  <span class="spacer"></span>
  <a href="op/op.Download.php?documentid=<?php echo $documentid; ?>&amp;version=<?php echo $objversion; ?>">下载原文件</a>
</div>
<?php
if ($parseError !== '') {
	echo '<div class="err">表格解析失败：' . htmlspecialchars($parseError) . '</div>';
} else {
	foreach ($sheets as $si => $sh) {
		$total = $sh['total'];
		$cols = $sh['cols'];
		$rows = $sh['rows'];
		$totalPages = max(1, (int)ceil($total / $PAGE_SIZE));
		$curPage = min($page, $totalPages);
		$start = ($curPage - 1) * $PAGE_SIZE;
		$slice = array_slice($rows, $start, $PAGE_SIZE);

		echo '<div class="sheet-title">工作表：' . htmlspecialchars($sh['title'])
			. ' （共 ' . $total . ' 行 × ' . $cols . ' 列）</div>';

		if ($totalPages > 1) {
			echo '<div class="pager">';
			$qs = 'documentid=' . $documentid . '&version=' . $objversion . '&page=';
			if ($curPage > 1)
				echo '<a href="?documentid=' . $documentid . '&version=' . $objversion . '&page=' . ($curPage - 1) . '">上一页</a>';
			// page number window
			$from = max(1, $curPage - 3);
			$to = min($totalPages, $curPage + 3);
			if ($from > 1) echo '<a href="?documentid=' . $documentid . '&version=' . $objversion . '&page=1">1</a>' . ($from > 2 ? '<span>…</span>' : '');
			for ($p = $from; $p <= $to; $p++) {
				if ($p == $curPage) echo '<span class="cur">' . $p . '</span>';
				else echo '<a href="?documentid=' . $documentid . '&version=' . $objversion . '&page=' . $p . '">' . $p . '</a>';
			}
			if ($to < $totalPages) echo ($to < $totalPages - 1 ? '<span>…</span>' : '') . '<a href="?documentid=' . $documentid . '&version=' . $objversion . '&page=' . $totalPages . '">' . $totalPages . '</a>';
			if ($curPage < $totalPages)
				echo '<a href="?documentid=' . $documentid . '&version=' . $objversion . '&page=' . ($curPage + 1) . '">下一页</a>';
			echo '<span>第 ' . $curPage . ' / ' . $totalPages . ' 页，每页 ' . $PAGE_SIZE . ' 行（共 ' . $total . ' 行）</span>';
			echo '</div>';
		}

		echo '<table class="tbl">';
		foreach ($slice as $row) {
			echo '<tr>';
			for ($c = 0; $c < $cols; $c++) {
				$val = isset($row[$c]) ? $row[$c] : '';
				echo '<td>' . htmlspecialchars((string)$val) . '</td>';
			}
			echo '</tr>';
		}
		echo '</table>';
	}
}
?>
</body>
</html>
