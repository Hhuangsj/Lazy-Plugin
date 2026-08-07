#!/usr/bin/env bash
# Shared validation for analysis output-directory names.

validate_analysis_output_name() {
    local name="${1-}"
    case "$name" in
        ''|.|..|*/*)
            echo "ERROR: OUT_NAME must be one non-empty directory name, not '.' or '..': $name" >&2
            return 2
            ;;
    esac
    return 0
}
