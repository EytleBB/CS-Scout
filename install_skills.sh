#!/bin/bash
# Install all non-deprecated skills from mattpocock/skills to ~/.claude/skills/
SRC="/tmp/mattpocock-skills/skills"
DEST="$HOME/.claude/skills"

installed=0
updated=0

for category in engineering in-progress misc personal productivity; do
  for skill in "$SRC/$category"/*/; do
    [ -d "$skill" ] || continue
    name=$(basename "$skill")
    target="$DEST/$name"
    if [ -d "$target" ]; then
      rm -rf "$target"
      updated=$((updated + 1))
      status="UPDATE"
    else
      installed=$((installed + 1))
      status="NEW"
    fi
    cp -r "$skill" "$target"
    echo "[$status] $name ($category)"
  done
done

echo ""
echo "=== Done: $installed new, $updated updated, $((installed + updated)) total ==="
