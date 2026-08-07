# resolve.sh —— manifest 加载与优先级仲裁。依赖 probe.sh / conda.sh 已被 source。

toolenv_tools_dir() {
    printf '%s\n' "${TOOLENV_TOOLS_DIR:-$TOOLENV_HOME/tools.d}"
}

toolenv_list_manifests() {
    local d f
    d=$(toolenv_tools_dir)
    [ -d "$d" ] || return 1
    for f in "$d"/*.sh; do
        [ -f "$f" ] || continue
        basename "$f" .sh
    done | sort
}

toolenv_load_manifest() {
    local tool=$1 f
    f="$(toolenv_tools_dir)/$tool.sh"
    [ -f "$f" ] || { echo "toolenv: 没有这个工具的 manifest: $tool ($f)" >&2; return 1; }
    TOOL_NAME=""; TOOL_DESC=""; TOOL_HINT=""
    unset -f tool_detect tool_activate tool_validate_path 2>/dev/null
    # shellcheck disable=SC1090
    . "$f" || return 1
    [ -n "$TOOL_NAME" ] || TOOL_NAME=$tool
    return 0
}

toolenv_load_overrides() {
    local f="${TOOLENV_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/toolenv}/overrides.sh"
    [ -f "$f" ] || return 0
    # shellcheck disable=SC1090
    . "$f"
}

_te_override_var() {   # 工具名 -> TOOLENV_XXX
    printf 'TOOLENV_%s' "$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"
}

# toolenv_resolve TOOL —— 解析一个工具
toolenv_resolve() {
    local tool=$1 ovar oval manifest_file manifest_loaded=0
    TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""
    ovar=$(_te_override_var "$tool")
    oval=${!ovar:-}

    # Load an existing manifest before honoring an explicit override so an
    # optional tool_validate_path hook can validate that override. If no
    # manifest exists, retain the historical override-only behavior.
    manifest_file="$(toolenv_tools_dir)/$tool.sh"
    if [ -f "$manifest_file" ]; then
        toolenv_load_manifest "$tool" || return 1
        manifest_loaded=1
    fi

    if [ -n "$oval" ]; then
        if [ -d "$oval" ]; then
            if [ "$manifest_loaded" = 1 ] \
                && declare -F tool_validate_path >/dev/null \
                && ! tool_validate_path "$oval"; then
                echo "toolenv: $ovar override failed manifest validation for $tool: $oval" >&2
                return 1
            fi
            _te_hit "$(readlink -f "$oval")" "override"
            return 0
        fi
        echo "toolenv: $ovar 指向的目录不存在: $oval" >&2
        return 1
    fi
    [ "$manifest_loaded" = 1 ] || toolenv_load_manifest "$tool" || return 1
    # manifest 里引用未设置的环境变量是常态(可选路径线索),别让 set -u 打断探测
    local had_u=0
    case "$-" in *u*) had_u=1; set +u ;; esac
    tool_detect
    [ "$had_u" = 1 ] && set -u
    [ -n "$TOOLENV_HIT" ]
}

# toolenv_activate_lines TOOL PATH [CONDA_ENV]
toolenv_activate_lines() {
    local tool=$1 path=$2 cenv=${3:-}
    toolenv_load_manifest "$tool" || return 1
    tool_activate "$path" "$cenv"
}
