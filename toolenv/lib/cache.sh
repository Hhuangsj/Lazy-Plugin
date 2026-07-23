# cache.sh —— 探测结果缓存。格式是可 source 的 bash 赋值,按 hostname 分文件。

TOOLENV_CACHE_DIR="${TOOLENV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/toolenv}"
_TE_CACHE_TOOLS="${_TE_CACHE_TOOLS:-}"

toolenv_cache_file() {
    printf '%s/%s.env\n' "$TOOLENV_CACHE_DIR" "$(hostname -s 2>/dev/null || hostname)"
}

_te_key() {   # 工具名 -> 变量名安全片段
    printf '%s' "$1" | tr -c 'A-Za-z0-9_' '_'
}

_te_quote() { # 单引号安全转义
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# toolenv_cache_put TOOL STATUS PATH SOURCE ENV
toolenv_cache_put() {
    local tool=$1 status=$2 path=$3 source=$4 cenv=$5 k
    k=$(_te_key "$tool")
    eval "_TE_R_${k}_NAME=\$tool"
    eval "_TE_R_${k}_STATUS=\$status"
    eval "_TE_R_${k}_PATH=\$path"
    eval "_TE_R_${k}_SOURCE=\$source"
    eval "_TE_R_${k}_ENV=\$cenv"
    case " $_TE_CACHE_TOOLS " in
        *" $tool "*) ;;
        *) _TE_CACHE_TOOLS="$_TE_CACHE_TOOLS $tool" ;;
    esac
}

toolenv_cache_flush() {
    local f tmp tool k
    f=$(toolenv_cache_file)
    mkdir -p "$(dirname "$f")" || return 1
    tmp="$f.tmp.$$"
    {
        echo "# toolenv cache v1 — 由 'toolenv probe' 生成,可安全删除"
        echo "# host=$(hostname 2>/dev/null) date=$(date -Iseconds 2>/dev/null)"
        echo "_TE_CACHE_TOOLS=$(_te_quote "$_TE_CACHE_TOOLS")"
        for tool in $_TE_CACHE_TOOLS; do
            k=$(_te_key "$tool")
            eval "echo \"_TE_R_${k}_NAME=\$(_te_quote \"\$_TE_R_${k}_NAME\")\""
            eval "echo \"_TE_R_${k}_STATUS=\$(_te_quote \"\$_TE_R_${k}_STATUS\")\""
            eval "echo \"_TE_R_${k}_PATH=\$(_te_quote \"\$_TE_R_${k}_PATH\")\""
            eval "echo \"_TE_R_${k}_SOURCE=\$(_te_quote \"\$_TE_R_${k}_SOURCE\")\""
            eval "echo \"_TE_R_${k}_ENV=\$(_te_quote \"\$_TE_R_${k}_ENV\")\""
        done
    } > "$tmp" || return 1
    mv -f "$tmp" "$f"
}

toolenv_cache_load() {
    local f
    f=$(toolenv_cache_file)
    [ -f "$f" ] || return 1
    # shellcheck disable=SC1090
    . "$f"
}

# toolenv_cache_get TOOL FIELD   (FIELD: STATUS|PATH|SOURCE|ENV|NAME)
toolenv_cache_get() {
    local tool=$1 field=$2 k var
    k=$(_te_key "$tool")
    var="_TE_R_${k}_NAME"
    [ -n "${!var:-}" ] || return 1
    var="_TE_R_${k}_${field}"
    printf '%s\n' "${!var:-}"
}

toolenv_cache_tools() {
    local t
    for t in $_TE_CACHE_TOOLS; do printf '%s\n' "$t"; done
}

toolenv_cache_clear_memory() {
    local tool k
    for tool in $_TE_CACHE_TOOLS; do
        k=$(_te_key "$tool")
        unset "_TE_R_${k}_NAME" "_TE_R_${k}_STATUS" "_TE_R_${k}_PATH" \
              "_TE_R_${k}_SOURCE" "_TE_R_${k}_ENV"
    done
    _TE_CACHE_TOOLS=""
}

toolenv_cache_clear() {
    toolenv_cache_clear_memory
    rm -f "$(toolenv_cache_file)"
}
