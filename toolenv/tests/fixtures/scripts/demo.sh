#!/usr/bin/env bash
# @name: demo
# @description: 演示脚本,验证元信息解析
# @requires: faketool, conda:demoenv
# @usage: demo.sh <dir>...
set -u
echo "demo ran with FAKETOOL_HOME=${FAKETOOL_HOME:-unset}"
