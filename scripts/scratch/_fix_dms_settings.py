import sys

path = "/home/www-data/seeddms60x/conf/settings.xml"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Enable convertToPdf
if 'convertToPdf="false"' in c:
    c = c.replace('convertToPdf="false"', 'convertToPdf="true"')
    print("1. convertToPdf: enabled")
else:
    print("1. convertToPdf: already enabled or not found")

# 2. Enable pdfviewer extension
old2 = 'name="pdfviewer" disable="true"'
new2 = 'name="pdfviewer" disable="false"'
if old2 in c:
    c = c.replace(old2, new2)
    # Also fix __disable__ param
    c = c.replace(
        'pdfviewer" disable="false"><parameter name="__disable__">1',
        'pdfviewer" disable="false"><parameter name="__disable__">0'
    )
    print("2. pdfviewer: enabled")
else:
    print("2. pdfviewer: already enabled or not found")

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
    c = c.replace('    <converters target="fulltext">', pdf_block + '    <converters target="fulltext">')
    print("3. PDF converters: added")
else:
    print("3. PDF converters: already exist")

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

# Verify
with open(path, "r", encoding="utf-8") as f:
    v = f.read()
all_ok = True
for p in ['convertToPdf="true"', 'pdfviewer" disable="false"', 'converters target="pdf"']:
    if p in v:
        print("  OK: " + p)
    else:
        print("  FAIL: " + p)
        all_ok = False

print("RESULT: " + ("ALL OK" if all_ok else "FAILED"))
sys.exit(0 if all_ok else 1)
