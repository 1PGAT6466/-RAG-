c = open('/var/lib/seeddms/conf/settings.xml').read()
print('convertToPdf=true' if 'convertToPdf="true"' in c else 'convertToPdf=false')
print('pdfviewer enabled' if 'pdfviewer" disable="false"' in c else 'pdfviewer disabled')
print('pdf converters' if 'converters target="pdf"' in c else 'no pdf converters')
