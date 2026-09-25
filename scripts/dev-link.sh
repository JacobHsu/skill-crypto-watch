#!/usr/bin/env sh
# Link skills/* into .claude/skills/ so Claude Code sessions in this repo load the
# working copy directly: edits take effect in the next session, no plugin reinstall.
#   sh scripts/dev-link.sh
set -e
repo="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$repo/.claude/skills"
for dir in "$repo"/skills/*/; do
  name="$(basename "$dir")"
  [ -f "$dir/SKILL.md" ] || continue
  link="$repo/.claude/skills/$name"
  if [ -e "$link" ]; then
    echo "exists: .claude/skills/$name"
  else
    ln -s "../../skills/$name" "$link"
    echo "linked: .claude/skills/$name -> skills/$name"
  fi
done
