"""Draw the Vesper app icon: packaging/icon.svg and packaging/icon.png (1024 px).

The mark is the same as the UI logo (apps/web/src/packages/ui-primitives/Logo.tsx): a "V"
followed by a terminal cursor, with the evening star above it, on a dark rounded tile.
Run after changing it (needs rsvg-convert: brew install librsvg): python packaging/make_icon.py
"""

import subprocess
from pathlib import Path

HERE = Path(__file__).parent
EMBER, AMBER = "#FF7A45", "#FFC34D"

# The mark, in a 64 x 64 box. Logo.tsx uses the same numbers.
MARK = (
    f'<path d="M15 19 L29 46 L43 19" fill="none" stroke="{EMBER}" stroke-width="7.5" '
    f'stroke-linecap="round" stroke-linejoin="round"/>'
    f'<rect x="40" y="41" width="11" height="5.5" rx="1.5" fill="{AMBER}"/>'
    f'<circle cx="50" cy="14" r="3.4" fill="{AMBER}"/>'
)

# macOS icon grid: an 824 px tile centred in 1024, corner radius about 22%.
SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#231B17"/>
      <stop offset="1" stop-color="#120E0C"/>
    </linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="184" fill="url(#tile)"/>
  <rect x="101.5" y="101.5" width="821" height="821" rx="182.5" fill="none" stroke="#FFFFFF" stroke-opacity=".08" stroke-width="3"/>
  <g transform="translate(149 182) scale(11)">{MARK}</g>
</svg>
"""

if __name__ == "__main__":
    (HERE / "icon.svg").write_text(SVG)
    subprocess.run(["rsvg-convert", "-w", "1024", "-h", "1024", str(HERE / "icon.svg"), "-o", str(HERE / "icon.png")],
                   check=True)
    print("wrote", HERE / "icon.png")
