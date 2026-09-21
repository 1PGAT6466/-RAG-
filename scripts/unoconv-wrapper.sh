#!/bin/bash
# unoconv wrapper - uses LibreOffice instead of unoconv
# Usage: unoconv -d document -e PageRange=1 -f pdf --stdout -v 'inputfile'
# We only need the last argument (input file) and -f format
INPUT_FILE="${@: -1}"
FORMAT="pdf"

# Parse -f argument
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) FORMAT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

TMPDIR=$(mktemp -d)
libreoffice --headless --convert-to "$FORMAT" --outdir "$TMPDIR" "$INPUT_FILE" 2>/dev/null
RESULT=$(ls "$TMPDIR"/*."$FORMAT" 2>/dev/null | head -1)
if [ -n "$RESULT" ]; then
    cat "$RESULT"
fi
rm -rf "$TMPDIR"
