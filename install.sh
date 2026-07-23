#!/usr/bin/env bash
# install.sh —— 把本仓库里的 skill 挂到 ~/.claude/skills/,并做一次工具探测。
# 换机器:git clone && ./install.sh
set -u

REPO=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

mkdir -p "$SKILLS_DIR" || exit 1

echo "== 挂载 skill 到 $SKILLS_DIR"
found=0
for d in "$REPO"/*/; do
    d=${d%/}
    [ -f "$d/SKILL.md" ] || continue
    found=1
    name=$(basename "$d")
    target="$SKILLS_DIR/$name"
    if [ -L "$target" ]; then
        if [ "$(readlink -f "$target")" = "$d" ]; then
            echo "  = $name(已挂载)"
        else
            ln -sfn "$d" "$target" && echo "  ~ $name(重指向)"
        fi
    elif [ -e "$target" ]; then
        echo "  ! $name:$target 已存在且不是 symlink,跳过。手工处理后重跑。" >&2
    else
        ln -s "$d" "$target" && echo "  + $name"
    fi
done
[ "$found" = 1 ] || echo "  (仓库里还没有含 SKILL.md 的目录)"

echo
echo "== 探测工具"
"$REPO/toolenv/toolenv" probe --force >/dev/null || exit 1
"$REPO/toolenv/toolenv" list

echo
echo "路径不对?写 ${XDG_CONFIG_HOME:-$HOME/.config}/toolenv/overrides.sh,例如:"
echo "  export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1"
echo "把 toolenv 加进 PATH(可选):export PATH=\"$REPO/toolenv:\$PATH\""
