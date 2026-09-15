import zipfile, re
from pathlib import Path
p = Path(r'.\FCA Handbook etc links V-1.0 .docx')
print('exists', p.exists())
if p.exists():
    with zipfile.ZipFile(p) as z:
        names = [n for n in z.namelist() if n.endswith('.xml')]
        text = ''
        for name in names:
            b = z.read(name)
            if b:
                text += b.decode('utf-8', 'ignore') + '\n'
        urls = sorted(set(re.findall(r'https?://[^\s<>"\']+', text)))
        print('URL COUNT', len(urls))
        for u in urls:
            print(u)
