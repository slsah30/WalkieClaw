# Builds index.html from src.html by inlining all four webfont faces as base64.
# Usage: python3 build.py    (then: node render.cjs index.html export slide)
import base64, pathlib
d = pathlib.Path(__file__).resolve().parent
fonts = {
    '__INTER_B64__':    d / 'fonts' / 'inter-latin.woff2',
    '__ARCHIVO_B64__':  d / 'fonts' / 'archivo-black-400.woff2',
    '__PLEX500_B64__':  d / 'fonts' / 'ibm-plex-mono-500.woff2',
    '__PLEX600_B64__':  d / 'fonts' / 'ibm-plex-mono-600.woff2',
}
out = (d / 'src.html').read_text()
for token, path in fonts.items():
    out = out.replace(token, base64.b64encode(path.read_bytes()).decode())
    assert token not in out, token
(d / 'index.html').write_text(out)
print('wrote index.html', len(out), 'bytes')
