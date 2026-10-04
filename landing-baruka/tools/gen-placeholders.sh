#!/usr/bin/env bash
# Genera imágenes de marcador 3:4 para la landing de Baruka Design.
# Reemplaza los .jpg de img/ por fotos reales (mismo nombre) y este script deja de importar.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="img"
W=900
H=1200

# Producto:color (tonos editoriales apagados, distintos entre sí)
declare -a ITEMS=(
  "hero:1A1A1F"
  "kendall:C9B8A8"
  "codman:2E3A46"
  "anika:D8CFC0"
  "kabanova:7A6A5A"
  "cristal:B9C2CC"
  "ankara:8A5A44"
  "azra:1C1C22"
  "elvi:A39787"
  "pandora:6E5B4E"
  "xela:3B4A6B"
  "begonia:8E3B4E"
  "paola:C4A484"
  "holly:4A5A3F"
  "editorial-1:D9D2C6"
  "editorial-2:2A2E3A"
)

gen() { # nombre color variante(0 normal,1 alt)
  local name="$1" hex="$2" variant="$3"
  local extra=""
  [ "$variant" = "1" ] && extra=",drawbox=x=0:y=${H}:w=${W}:h=-${H}:color=black@0.18:t=fill"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "color=c=0x${hex}:s=${W}x${H}" \
    -vf "drawbox=x=48:y=48:w=$((W-96)):h=$((H-96)):color=white@0.05:t=2${extra}" \
    -frames:v 1 -q:v 4 "${OUT}/${name}.jpg"
  printf '  %s.jpg\n' "$name"
}

echo "Generando marcadores en ${OUT}/"
for it in "${ITEMS[@]}"; do
  gen "${it%%:*}" "${it##*:}" 0
done
# Segunda foto (hover/tap) sólo para las prendas de la galería.
for p in kendall codman anika kabanova cristal ankara azra elvi pandora xela begonia paola holly; do
  color=$(for it in "${ITEMS[@]}"; do [ "${it%%:*}" = "$p" ] && { echo "${it##*:}"; break; }; done)
  gen "${p}_alt" "$color" 1
done
echo "Listo: $(ls -1 ${OUT}/*.jpg | wc -l | tr -d ' ') imágenes."
