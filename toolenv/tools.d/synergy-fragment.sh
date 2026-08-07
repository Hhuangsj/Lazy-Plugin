TOOL_NAME="synergy-fragment"
TOOL_DESC="Synergy-Fragment peptide residue atom mapper (read-only)"
TOOL_HINT="Set SYNERGY_FRAGMENT_DIR or TOOLENV_SYNERGY_FRAGMENT to a Synergy-Fragment checkout"

tool_validate_path() {
    local root=$1
    [ -d "$root" ] \
        && [ -f "$root/peptide_sequence.py" ] \
        && [ -f "$root/monomer_library_nonstandard_segments_simple.csv" ]
}

_synergy_fragment_try() {
    local root=$1 source=$2
    [ -n "$root" ] || return 1
    tool_validate_path "$root" || return 1
    _te_hit "$(readlink -f "$root")" "$source"
}

tool_detect() {
    [ -n "$TOOLENV_HIT" ] && return 0

    _synergy_fragment_try "${SYNERGY_FRAGMENT_DIR:-}" \
        "env:SYNERGY_FRAGMENT_DIR" && return 0
    _synergy_fragment_try "${TOOLENV_SYNERGY_FRAGMENT:-}" \
        "env:TOOLENV_SYNERGY_FRAGMENT" && return 0

    local candidate
    for candidate in \
        "$HOME/workstations/Synergy/Synergy-Fragment" \
        "$HOME/Synergy/Synergy-Fragment" \
        "/opt/Synergy/Synergy-Fragment"; do
        _synergy_fragment_try "$candidate" "path:$candidate" && return 0
    done
    return 1
}

tool_activate() {
    printf 'export SYNERGY_FRAGMENT_DIR=%q\n' "$1"
}
