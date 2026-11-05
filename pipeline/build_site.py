"""Arma la carpeta _site/ que publica GitHub Pages.

index.html se escribe sin <!doctype> ni <html> (así también se publica como Artifact de Claude),
así que aquí se le agrega el envoltorio para que el navegador no entre en modo quirks.

Uso: python3 pipeline/build_site.py
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"

if SITE.exists():
    shutil.rmtree(SITE)
(SITE / "data").mkdir(parents=True)

page = (ROOT / "index.html").read_text(encoding="utf-8")
(SITE / "index.html").write_text(f'<!doctype html>\n<html lang="es">\n{page}\n</html>\n', encoding="utf-8")
shutil.copy(ROOT / "data" / "fuel.json", SITE / "data" / "fuel.json")
(SITE / ".nojekyll").touch()
print(f"Listo: {SITE}")
