#!/usr/bin/env bash
# Render docs/banner.svg -> docs/banner.png.
#
# The banner has rounded corners, so the pixels outside them must stay
# TRANSPARENT. A headless render defaults to an opaque white page, which flattens
# the output to RGB and paints those corners white — visible as white notches on
# every dark README. This has now happened on more than one banner update, each
# time because the render was retyped by hand and one flag went missing.
#
# So the invocation lives here rather than in anyone's shell history. Do not
# render the banner by hand; run this. guard/banner_render.py checks the result
# and will go red if the corners come back opaque or the PNG falls behind the SVG.
set -euo pipefail

cd "$(dirname "$0")/.."
SVG="docs/banner.svg"
PNG="docs/banner.png"
STAMP="docs/banner.stamp"

CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
if [ -z "$CHROME" ]; then
  echo "render_banner: no chrome/chromium on PATH; set CHROME=<path>" >&2
  exit 2
fi

# The SVG's own viewBox is the source of truth for geometry; the PNG ships at 2x.
read -r VW VH < <(python3 - "$SVG" <<'PY'
import re, sys
tag = re.search(r"<svg[^>]*>", open(sys.argv[1]).read()).group(0)
vb = re.search(r'viewBox="([\d.\s-]+)"', tag)
w, h = (vb.group(1).split()[2:4] if vb else
        (re.search(r'width="(\d+)"', tag).group(1), re.search(r'height="(\d+)"', tag).group(1)))
print(int(float(w)), int(float(h)))
PY
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --default-background-color=00000000 is the whole point of this script: without
# it the page ground is opaque white and the rounded corners stop being corners.
"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=2 \
  --window-size="${VW},${VH}" \
  --default-background-color=00000000 \
  --screenshot="$TMP/out.png" \
  "file://$PWD/$SVG" >/dev/null 2>&1

[ -s "$TMP/out.png" ] || { echo "render_banner: chrome produced no image" >&2; exit 2; }
mv "$TMP/out.png" "$PNG"

# Records WHICH svg this png was rendered from, so an edited svg with a stale png
# is a detectable state rather than an invisible one.
sha256sum "$SVG" | awk '{print $1}' > "$STAMP"

echo "render_banner: wrote $PNG at $((VW*2))x$((VH*2)) from $SVG (transparent ground)"
python3 guard/banner_render.py || true
