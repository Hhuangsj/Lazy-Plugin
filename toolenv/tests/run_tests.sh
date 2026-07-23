#!/usr/bin/env bash
# 跑全部 toolenv 测试。用法:toolenv/tests/run_tests.sh [test_probe.sh ...]
set -u

TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
export TOOLENV_HOME=$(dirname "$TESTS_DIR")

files=("$@")
if [ ${#files[@]} -eq 0 ]; then
    files=("$TESTS_DIR"/test_*.sh)
fi

rc=0
for f in "${files[@]}"; do
    [ -f "$f" ] || f="$TESTS_DIR/$f"
    echo "== $(basename "$f")"
    bash "$f" || rc=1
done

if [ "$rc" -eq 0 ]; then echo "ALL PASS"; else echo "FAILURES" >&2; fi
exit "$rc"
