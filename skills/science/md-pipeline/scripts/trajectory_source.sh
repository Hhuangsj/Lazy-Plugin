#!/usr/bin/env bash
# Shared raw/Align trajectory-pair selection for md-pipeline entry points.

select_trajectory_pair() {
    local dir="${1:-}"
    local source="${2:-raw}"
    local align_cms="${3-${ALIGN_CMS:-}}"
    local align_trj="${4-${ALIGN_TRJ:-}}"
    local raw_cms="${5-${RAW_CMS:-}}"
    local raw_trj="${6-${RAW_TRJ:-}}"
    local cms=""
    local trj=""
    local base=""
    local candidate candidate_base
    local old_nullglob
    local -a candidates=()

    if [ -z "$dir" ] || [ ! -d "$dir" ]; then
        echo "ERROR: trajectory directory does not exist: ${dir:-<empty>}" >&2
        return 1
    fi
    dir="$(cd "$dir" 2>/dev/null && pwd)" || {
        echo "ERROR: trajectory directory is not accessible: $dir" >&2
        return 1
    }

    resolve_inside_dir() {
        local path="$1"
        case "$path" in
            /*) printf '%s\n' "$path" ;;
            *) printf '%s\n' "$dir/$path" ;;
        esac
    }

    case "$source" in
        raw)
            if [ -n "$raw_cms" ]; then
                cms="$(resolve_inside_dir "$raw_cms")"
                if [ ! -f "$cms" ]; then
                    echo "ERROR: explicit raw CMS does not exist: $cms" >&2
                    return 1
                fi
            else
                old_nullglob="$(shopt -p nullglob)"
                shopt -s nullglob
                for candidate in "$dir"/*-out.cms; do
                    candidate_base="${candidate##*/}"
                    [[ "$candidate_base" == *_ALIGN-out.cms ]] && continue
                    [[ "$candidate_base" == PL_Analysis* ]] && continue
                    [[ "$candidate_base" =~ _[0-9]+-out\.cms$ ]] && continue
                    candidate_base="${candidate%-out.cms}"
                    [ -d "${candidate_base}_trj" ] && candidates+=("$candidate")
                done
                eval "$old_nullglob"

                if [ "${#candidates[@]}" -eq 0 ]; then
                    echo "ERROR: no complete raw *-out.cms + *_trj pair found in $dir" >&2
                    return 1
                fi
                if [ "${#candidates[@]}" -gt 1 ]; then
                    echo "ERROR: multiple raw *-out.cms files found; set RAW_CMS (and optionally RAW_TRJ):" >&2
                    printf '  %s\n' "${candidates[@]##*/}" >&2
                    return 1
                fi
                cms="${candidates[0]}"
            fi

            base="${cms##*/}"
            base="${base%-out.cms}"
            if [ -n "$raw_trj" ]; then
                trj="$(resolve_inside_dir "$raw_trj")"
            else
                trj="${cms%-out.cms}_trj"
            fi
            ;;
        align)
            if [ -n "$align_cms" ]; then
                cms="$(resolve_inside_dir "$align_cms")"
                if [ ! -f "$cms" ]; then
                    echo "ERROR: explicit Align CMS does not exist: $cms" >&2
                    return 1
                fi
                if [ -n "$align_trj" ]; then
                    trj="$(resolve_inside_dir "$align_trj")"
                else
                    base="${cms%-out.cms}"
                    trj="${base}_trj"
                fi
                if [ ! -d "$trj" ]; then
                    echo "ERROR: matching Align trajectory does not exist: $trj" >&2
                    return 1
                fi
            else
                old_nullglob="$(shopt -p nullglob)"
                shopt -s nullglob
                for candidate in "$dir"/*_ALIGN-out.cms; do
                    candidate_base="${candidate%-out.cms}"
                    if [ -d "${candidate_base}_trj" ]; then
                        candidates+=("$candidate")
                    fi
                done
                eval "$old_nullglob"

                if [ "${#candidates[@]}" -eq 0 ]; then
                    echo "ERROR: no complete *_ALIGN-out.cms + *_ALIGN_trj pair found in $dir" >&2
                    return 1
                fi
                if [ "${#candidates[@]}" -gt 1 ]; then
                    echo "ERROR: multiple Align trajectory pairs found; set ALIGN_CMS (and optionally ALIGN_TRJ):" >&2
                    printf '  %s\n' "${candidates[@]##*/}" >&2
                    return 1
                fi

                cms="${candidates[0]}"
                trj="${cms%-out.cms}_trj"
            fi

            base="${cms##*/}"
            base="${base%-out.cms}"
            ;;
        *)
            echo "ERROR: unknown TRAJECTORY_SOURCE='$source'; expected raw or align" >&2
            return 1
            ;;
    esac

    if [ ! -f "$cms" ]; then
        echo "ERROR: selected CMS does not exist: $cms" >&2
        return 1
    fi
    if [ ! -d "$trj" ]; then
        echo "ERROR: selected trajectory directory does not exist: $trj" >&2
        return 1
    fi

    SELECTED_CMS="$cms"
    SELECTED_TRJ="$trj"
    SELECTED_BASE="$base"
    export SELECTED_CMS SELECTED_TRJ SELECTED_BASE
}
