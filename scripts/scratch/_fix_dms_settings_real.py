import sys

path = "/var/lib/seeddms/conf/settings.xml"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

changed = []

# 1. Enable convertToPdf
if 'convertToPdf="false"' in c:
    c = c.replace('convertToPdf="false"', 'convertToPdf="true"')
    changed.append("convertToPdf=true")

# 2. Enable pdfviewer extension
old2 = 'name="pdfviewer" disable="true"'
new2 = 'name="pdfviewer" disable="false"'
if old2 in c:
    c = c.replace(old2, new2)
    changed.append("pdfviewer enabled")

# Also fix __disable__ param for pdfviewer
old3 = 'pdfviewer" disable="false"><parameter name="__disable__">1'
new3 = 'pdfviewer" disable="false"><parameter name="__disable__">0'
if old3 in c:
    c = c.replace(old3, new3)
    changed.append("pdfviewer __disable__=0")

# 3. Add PDF converters
if 'converters target="pdf"' not in c:
    mts = [
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "text/rtf",
    ]
    lines = ['    <converters target="pdf">']
    for mt in mts:
        lines.append("     <converter mimeType=\"" + mt + "\">unoconv -f pdf --stdout -v '%f'</converter>")
    lines.append("    </converters>")
    pdf_block = "\n".join(lines) + "\n"
    if '    <converters target="fulltext">' in c:
        c = c.replace('    <converters target="fulltext">', pdf_block + '    <converters target="fulltext">')
        changed.append("pdf converters added")
    else:
        print("WARN: fulltext converters anchor not found, pdf converters NOT added")
        sys.exit(1)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

print("Changes applied:", ", ".join(changed) if changed else "(none - already applied)")

# Verify
with open(path, "r", encoding="utf-8") as f:
    v = f.read()
print("convertToPdf=true:", 'convertToPdf="true"' in v)
print("pdfviewer enabled:", 'pdfviewer" disable="false"' in v)
print("pdf converters:", 'converters target="pdf"' in v)
