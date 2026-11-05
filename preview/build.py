"""Rebuild an isolated static preview from the same production frontend."""
import shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]
destination=root/'preview'/'dist'
if destination.exists():shutil.rmtree(destination)
(destination/'static').mkdir(parents=True)
shutil.copytree(root/'frontend',destination/'static',dirs_exist_ok=True)
html=(root/'frontend'/'index.html').read_text(encoding='utf-8')
html=html.replace('</head>', '<script src="/demo.js"></script></head>')
(destination/'index.html').write_text(html,encoding='utf-8',newline='')
shutil.copy2(root/'preview'/'demo.js',destination/'demo.js')
print(destination)
