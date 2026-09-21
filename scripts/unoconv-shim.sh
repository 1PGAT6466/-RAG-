#!/bin/bash
# unoconv shim - translates unoconv calls to LibreOffice
# Supports: unoconv -f pdf [--stdout] -v 'inputfile'
STDOUT_MODE=0
FORMAT="pdf"
INPUT_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) FORMAT="$2"; shift 2 ;;
        --stdout) STDOUT_MODE=1; shift ;;
        -v) shift ;;
        -d) shift; shift ;;  # skip -d document
        -e) shift; shift ;;  # skip -e PageRange=1
        -* ) shift ;;
        *) INPUT_FILE="$1"; shift ;;
    esac
done

if [ -z "$INPUT_FILE" ]; then
    echo "Usage: unoconv -f pdf [--stdout] 'inputfile'" >&2
    exit 1
fi

TMPDIR=$(mktemp -d)
libreoffice --headless --convert-to "$FORMAT" --outdir "$TMPDIR" "$INPUT_FILE" >/dev/null 2>&1

BASENAME=$(basename "$INPUT_FILE")
BASENAME_NOEXT="${BASENAME%.*}"
RESULT="$TMPDIR/${BASENAME_NOEXT}.${FORMAT}"

if [ ! -f "$RESULT" ]; then
    # Try finding any output file
    RESULT=$(ls "$TMPDIR"/*."$FORMAT" 2>/dev/null | head -1)
fi

if [ -n "$RESULT" ] && [ -f "$RESULT" ]; then
    if [ "$STDOUT_MODE" -eq 1 ]; then
        cat "$RESULT"
    else
        cp "$RESULT" "$(dirname "$INPUT_FILE")/${BASENAME_NOEXT}.${FORMAT}"
    fi
fi

rm -rf "$TMPDIR"
