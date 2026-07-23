# meta.sh —— 解析脚本头的 "# @key: value" 元信息。
# 只扫描文件开头的注释区:遇到第一行既非注释也非空行即停止。

toolenv_meta_get() {
    local file=$1 key=$2 line val=""
    [ -f "$file" ] || return 1
    while IFS= read -r line; do
        case "$line" in
            '#!'*) continue ;;
            '#'*)  ;;
            '')    continue ;;
            *)     break ;;
        esac
        case "$line" in
            "# @$key:"*)
                val=${line#"# @$key:"}
                # 去掉首尾空白
                val=${val#"${val%%[![:space:]]*}"}
                val=${val%"${val##*[![:space:]]}"}
                printf '%s\n' "$val"
                return 0
                ;;
        esac
    done < "$file"
    return 1
}

# toolenv_meta_requires FILE —— 逗号分隔转空格分隔
toolenv_meta_requires() {
    local file=$1 raw
    raw=$(toolenv_meta_get "$file" requires) || return 0
    printf '%s\n' "$raw" | tr ',' ' ' | tr -s '[:space:]' ' ' \
        | sed 's/^ *//; s/ *$//'
}
