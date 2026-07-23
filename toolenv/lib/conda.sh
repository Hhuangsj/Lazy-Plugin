# conda.sh —— conda 安装与环境的发现。依赖 probe.sh 已被 source。

# toolenv_conda_root —— 打印 conda 根目录;找不到返回 1
toolenv_conda_root() {
    local c p
    if [ -n "${TOOLENV_CONDA:-}" ] && [ -d "${TOOLENV_CONDA}" ]; then
        readlink -f "$TOOLENV_CONDA"; return 0
    fi
    if [ -n "${CONDA_ROOT:-}" ] && [ -d "${CONDA_ROOT}" ]; then
        readlink -f "$CONDA_ROOT"; return 0
    fi
    if [ -n "${CONDA_EXE:-}" ] && [ -x "${CONDA_EXE}" ]; then
        # <root>/bin/conda 或 <root>/condabin/conda
        readlink -f "$(dirname "$(dirname "$(readlink -f "$CONDA_EXE")")")"; return 0
    fi
    if c=$(command -v conda 2>/dev/null) && [ -n "$c" ]; then
        readlink -f "$(dirname "$(dirname "$(readlink -f "$c")")")"; return 0
    fi
    for p in "$HOME"/miniforge3 "$HOME"/mambaforge "$HOME"/miniconda3 "$HOME"/anaconda3 \
             /opt/miniforge3 /opt/miniconda3 /opt/anaconda3; do
        if [ -d "$p/envs" ] || [ -x "$p/condabin/conda" ]; then
            readlink -f "$p"; return 0
        fi
    done
    return 1
}

# toolenv_conda_envs —— 每行 "名字<TAB>前缀";base 排第一
toolenv_conda_envs() {
    local root d name
    root=$(toolenv_conda_root) || return 1
    printf 'base\t%s\n' "$root"
    for d in "$root"/envs/*/; do
        [ -d "$d" ] || continue
        d=${d%/}
        name=$(basename "$d")
        printf '%s\t%s\n' "$name" "$d"
    done
    # 装在根目录之外的环境(conda create -p)
    if [ -f "$HOME/.conda/environments.txt" ]; then
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            [ -d "$d" ] || continue
            case "$d" in "$root"|"$root"/envs/*) continue ;; esac
            printf '%s\t%s\n' "$(basename "$d")" "$d"
        done < "$HOME/.conda/environments.txt"
    fi
}

# toolenv_conda_has_env NAME
toolenv_conda_has_env() {
    local want=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ "$name" = "$want" ] && return 0
    done < <(toolenv_conda_envs)
    return 1
}

# try_conda_env_bin EXE —— 哪个环境的 bin/EXE 可执行
try_conda_env_bin() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local exe=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ -x "$prefix/bin/$exe" ] || continue
        _te_hit "$prefix" "conda:$name" "$name"
        return 0
    done < <(toolenv_conda_envs)
    return 1
}

# try_conda_env_python IMPORT_STMT —— 哪个环境的 python 能跑通这句 import
try_conda_env_python() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local stmt=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ -x "$prefix/bin/python" ] || continue
        "$prefix/bin/python" -c "$stmt" >/dev/null 2>&1 || continue
        _te_hit "$prefix" "conda:$name" "$name"
        return 0
    done < <(toolenv_conda_envs)
    return 1
}
