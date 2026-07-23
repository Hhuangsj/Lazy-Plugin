#!/usr/bin/env bash
#
# 用法：
#   cd /data1/home/huangshengjie/workstations/RecA/PEPX_P8P9
#
#   # 只检查会执行哪些命令，不真正提交 MD 或分析任务。
#   ./run_serial_md.sh --dry-run
#
#   # 后台串行运行：自动寻找连续空闲的 GPU，跑完一个结构再跑下一个。
#   nohup ./run_serial_md.sh > run_serial_md.nohup.log 2>&1 &
#
#   # 后台串行运行并指定 GPU，例如只用 GPU 2。
#   nohup ./run_serial_md.sh --gpu 2 > run_serial_md.nohup.log 2>&1 &
#
#   # 多 GPU 并行：GPU 0 和 GPU 2 各自启动一个 worker，每张卡内部仍串行。
#   nohup ./run_serial_md.sh --gpus 0,2 > run_serial_md.nohup.log 2>&1 &
#
#   # 调整 GPU 空闲检查：连续检查 3 次，每次间隔 100 秒；没有空闲 GPU 时 600 秒后再扫一轮。
#   ./run_serial_md.sh --gpu-stable-checks 3 --gpu-check-interval 100 --sleep 600
#
# 说明：
#   - 输入结构来自 md_pending_serial.list，每行一个 .mae 文件名。
#   - 已完成和失败的结构分别记录到 md_completed_serial.list 和 md_failed_serial.list。
#   - 启动下一个 MD 前，会等待上一轮 GPU work 退出，并确认 GPU 利用率/显存连续保持空闲。

# 环境隔离：统一从同目录 env.sh 加载 Schrödinger/Desmond/AutoMD 与隔离的 conda 环境，
# 使脚本在 nohup/cron/任意 shell 下都能稳定运行(必须在 set -e 之前 source)。
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"

set -euo pipefail

# 路径参数：默认都放在脚本所在目录，通常不需要改。
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PENDING_LIST="${WORKDIR}/md_pending_serial.list"
COMPLETED_LIST="${WORKDIR}/md_completed_serial.list"
FAILED_LIST="${WORKDIR}/md_failed_serial.list"
LOG_FILE="${WORKDIR}/run_serial_md.log"

# 运行模式参数。
# GPU_ID 为空时自动扫描所有 GPU；设置后只等待并使用指定 GPU，例如 --gpu 2。
GPU_ID=""
# GPU_IDS 不为空时启用多 worker 模式，例如 --gpus 0,2。
# 每个 GPU 一个 worker，各自串行跑；worker 之间用锁认领任务，避免重复提交同一个结构。
GPU_IDS=""
# DRY_RUN=true 时只打印命令，不提交任务、不更新完成/失败清单。
DRY_RUN=false
# 没有找到空闲 GPU 时，等待多少秒后重新扫描。
SLEEP_SECONDS=600

# GPU 空闲判定阈值。
# 显存占用必须 <= MAX_FREE_MEM_MB，避免接上仍残留大任务的 GPU。
MAX_FREE_MEM_MB=100
# GPU 利用率必须 <= MAX_FREE_UTIL，避免提交到仍在计算的 GPU。
MAX_FREE_UTIL=5
# 必须连续多少次都满足空闲条件，才认为上一轮 work 完全结束。
GPU_STABLE_CHECKS=3
# 连续空闲检查之间的等待秒数，用来避开 gdesmond 刚退出后的短暂抖动。
GPU_CHECK_INTERVAL=100

# MD 命令模板：-i 输入文件会在运行时插入；其他参数沿用当前项目设置。
# 模拟时长(-t, ns)与轨迹帧数(-o)可用环境变量覆盖,默认与历史一致(200ns/2000帧)。
#   MDTIME=500 FRAMES=2000 ./run_serial_md.sh ...   # 500ns、2000帧
AUTOMD_CMD=(AutoMD -S OUC -P "protein" -L "res.ptype UNK" -F OPLS4 -o "${FRAMES:-2000}" -t "${MDTIME:-200}" -G localhost)
# 轨迹分析命令模板：通配符必须在 MD 目录里由 shell 展开，所以这里保存成字符串。
# 注意两处经验修正(见 README「踩坑记录」)：
#   1) 配体用 -L "res.ptype UNK"(与 AutoMD 建模一致)；默认 "ligand" 对多肽类配体选不到原子。
#   2) 默认不含 MMGBSA：CLEAN 去水去离子后冻结集为空会报错。要跑请在 -M 末尾加 "+MMGBSA"
#      并去掉 thermal_mmgbsa 的 -frozen/-atom_asl,或改用独立的 run_analysis.sh。
AUTOTRJ_SHELL_CMD='AutoTRJ -i *md_trj -J PL_Analysis -M "APCluster_5+LigandAPCluster_5+LigandCHCluster_5_1.0" -L "res.ptype UNK" -t "1:2001:20" -C "not solvent and not ions" -a'

# 多 worker 任务认领用的锁文件。CLAIMED_LIST 会在 main 里创建为本次运行的临时文件。
LOCK_FILE=""
CLAIMED_LIST=""

usage() {
  cat <<EOF
Usage: ./run_serial_md.sh [options]

Options:
  --workdir DIR       .mae 文件所在目录。默认：脚本所在目录。
  --list FILE         待运行清单。默认：md_pending_serial.list。
  --completed FILE    已完成记录文件。默认：md_completed_serial.list。
  --failed FILE       失败记录文件。默认：md_failed_serial.list。
  --gpu INDEX         只使用指定 GPU；提交前仍会检查这张卡是否真的空闲。
  --gpus LIST         多 GPU 并行模式，例如 0,2；每张 GPU 一个 worker，各自串行运行。
  --dry-run           只打印命令，不真正运行 MD/分析，也不更新完成/失败记录。
  --sleep SECONDS     没有空闲 GPU 时的轮询间隔。默认：600。
  --gpu-stable-checks N
                      GPU 必须连续 N 次空闲才可使用。默认：3。
  --gpu-check-interval SECONDS
                      连续空闲检查之间的等待秒数。默认：100。
  --max-free-util N   GPU 利用率必须 <= N%。默认：5。
  --max-free-mem-mb N GPU 显存占用必须 <= N MiB。默认：100。
  -h, --help          显示帮助。
EOF
}

log() {
  local msg="[$(date '+%F %T')] $*"
  echo "${msg}" | tee -a "${LOG_FILE}" >&2
}

quote_cmd() {
  printf "%q " "$@"
  printf "\n"
}

is_listed() {
  local item="$1"
  local file="$2"
  [[ -f "${file}" ]] && awk 'NF && $1 !~ /^#/ {print $1}' "${file}" | grep -Fxq "${item}"
}

has_existing_md_dir() {
  local mae="$1"
  local base="${mae%.mae}"
  compgen -G "${WORKDIR}/${base}-*-md" > /dev/null
}

gpu_uuid_for_index() {
  local index="$1"
  nvidia-smi --query-gpu=index,uuid --format=csv,noheader,nounits |
    awk -F, -v idx="${index}" '$1 + 0 == idx {gsub(/^ +| +$/, "", $2); print $2}'
}

gpu_compute_apps_for_index() {
  local index="$1"
  local uuid
  uuid="$(gpu_uuid_for_index "${index}")"
  [[ -n "${uuid}" ]] || return 1

  nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null |
    awk -F, -v uuid="${uuid}" '
      {
        for (i = 1; i <= NF; i++) {
          gsub(/^ +| +$/, "", $i)
        }
        if ($1 == uuid) {
          print "pid=" $2 ", process=" $3 ", mem=" $4 "MiB"
        }
      }'
}

gpu_free_sample() {
  local index="$1"
  local check_number="$2"
  local uuid apps stats util mem

  uuid="$(gpu_uuid_for_index "${index}")"
  if [[ -z "${uuid}" ]]; then
    log "GPU ${index} not found."
    return 1
  fi

  apps="$(gpu_compute_apps_for_index "${index}" || true)"
  if [[ -n "${apps}" ]]; then
    log "GPU ${index} busy: previous work still has active compute app(s): ${apps//$'\n'/; }."
    return 1
  fi

  stats="$(nvidia-smi --id="${index}" --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits)"
  util="$(awk -F, '{gsub(/^ +| +$/, "", $1); print $1}' <<< "${stats}")"
  mem="$(awk -F, '{gsub(/^ +| +$/, "", $2); print $2}' <<< "${stats}")"

  if [[ "${util}" -le "${MAX_FREE_UTIL}" && "${mem}" -le "${MAX_FREE_MEM_MB}" ]]; then
    log "GPU ${index} stable free check ${check_number}/${GPU_STABLE_CHECKS}: util=${util}%, mem=${mem}MiB."
    return 0
  fi

  log "GPU ${index} busy: util=${util}% (limit ${MAX_FREE_UTIL}%), mem=${mem}MiB (limit ${MAX_FREE_MEM_MB}MiB)."
  return 1
}

gpu_is_free() {
  local index="$1"
  local check
  for ((check = 1; check <= GPU_STABLE_CHECKS; check++)); do
    if ! gpu_free_sample "${index}" "${check}"; then
      return 1
    fi
    if [[ "${check}" -lt "${GPU_STABLE_CHECKS}" ]]; then
      sleep "${GPU_CHECK_INTERVAL}"
    fi
  done
}

find_free_gpu() {
  local index
  if [[ -n "${GPU_ID}" ]]; then
    if gpu_is_free "${GPU_ID}"; then
      echo "${GPU_ID}"
      return 0
    fi
    return 1
  fi

  while IFS=, read -r index _; do
    index="$(awk '{print $1}' <<< "${index}")"
    if gpu_is_free "${index}"; then
      echo "${index}"
      return 0
    fi
  done < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader,nounits)

  return 1
}

wait_for_free_gpu() {
  local gpu
  while true; do
    if gpu="$(find_free_gpu)"; then
      echo "${gpu}"
      return 0
    fi
    log "No free GPU found; sleeping ${SLEEP_SECONDS}s."
    sleep "${SLEEP_SECONDS}"
  done
}

wait_for_gpu() {
  local gpu="$1"
  while true; do
    if gpu_is_free "${gpu}"; then
      echo "${gpu}"
      return 0
    fi
    log "GPU ${gpu} is not free; sleeping ${SLEEP_SECONDS}s."
    sleep "${SLEEP_SECONDS}"
  done
}

latest_md_dir_for() {
  local mae="$1"
  local base="${mae%.mae}"
  local latest=""
  local dir
  for dir in "${WORKDIR}/${base}"-*-md; do
    [[ -d "${dir}" ]] || continue
    latest="${dir}"
  done
  [[ -n "${latest}" ]] && echo "${latest}"
}

run_or_print() {
  if [[ "${DRY_RUN}" == true ]]; then
    quote_cmd "$@"
  else
    "$@"
  fi
}

run_shell_or_print() {
  local cmd="$1"
  if [[ "${DRY_RUN}" == true ]]; then
    echo "${cmd}"
  else
    bash -lc "${cmd}"
  fi
}

record_status() {
  local file="$1"
  local mae="$2"
  if [[ "${DRY_RUN}" == true ]]; then
    log "DRY-RUN: would record ${mae} in ${file}."
  else
    (
      flock 9
      if ! is_listed "${mae}" "${file}"; then
        echo "${mae}" >> "${file}"
      fi
    ) 9>"${LOCK_FILE}"
  fi
}

claim_next_task() {
  local worker_gpu="$1"
  local mae
  (
    flock 9
    while IFS= read -r mae || [[ -n "${mae}" ]]; do
      mae="${mae%%#*}"
      mae="$(awk '{$1=$1; print}' <<< "${mae}")"
      [[ -n "${mae}" ]] || continue

      if is_listed "${mae}" "${COMPLETED_LIST}"; then
        log "Worker GPU ${worker_gpu}: skipping completed ${mae}."
        continue
      fi
      if is_listed "${mae}" "${FAILED_LIST}"; then
        log "Worker GPU ${worker_gpu}: skipping failed ${mae}."
        continue
      fi
      if is_listed "${mae}" "${CLAIMED_LIST}"; then
        continue
      fi
      if has_existing_md_dir "${mae}"; then
        log "Worker GPU ${worker_gpu}: skipping ${mae}; existing MD directory found."
        continue
      fi
      if [[ ! -f "${WORKDIR}/${mae}" ]]; then
        log "Worker GPU ${worker_gpu}: ERROR missing input ${mae}."
        if [[ "${DRY_RUN}" == true ]]; then
          log "DRY-RUN: would record ${mae} in ${FAILED_LIST}."
        elif ! is_listed "${mae}" "${FAILED_LIST}"; then
          echo "${mae}" >> "${FAILED_LIST}"
        fi
        continue
      fi

      echo "${mae}" >> "${CLAIMED_LIST}"
      echo "${mae}"
      exit 0
    done < "${PENDING_LIST}"
    exit 1
  ) 9>"${LOCK_FILE}"
}

worker_loop() {
  local gpu="$1"
  local mae

  log "Starting worker for GPU ${gpu}."
  while mae="$(claim_next_task "${gpu}")"; do
    [[ -n "${mae}" ]] || break

    if [[ "${DRY_RUN}" == true ]]; then
      wait_for_gpu "${gpu}" > /dev/null
    else
      wait_for_gpu "${gpu}" > /dev/null
    fi

    if run_md_and_analysis "${mae}" "${gpu}"; then
      record_status "${COMPLETED_LIST}" "${mae}"
      log "Worker GPU ${gpu}: completed ${mae}."
    else
      record_status "${FAILED_LIST}" "${mae}"
      log "Worker GPU ${gpu}: FAILED ${mae}; continuing with remaining tasks."
    fi
  done
  log "Worker for GPU ${gpu} has no more tasks."
}

run_multi_gpu_workers() {
  local raw_gpu gpu
  local -a raw_gpus=()
  local -a pids=()
  local status=0

  IFS=',' read -ra raw_gpus <<< "${GPU_IDS}"
  for raw_gpu in "${raw_gpus[@]}"; do
    gpu="$(awk '{$1=$1; print}' <<< "${raw_gpu}")"
    [[ -n "${gpu}" ]] || continue
    worker_loop "${gpu}" &
    pids+=("$!")
  done

  if [[ "${#pids[@]}" -eq 0 ]]; then
    echo "No GPU indexes provided to --gpus." >&2
    exit 2
  fi

  for pid in "${pids[@]}"; do
    if ! wait "${pid}"; then
      status=1
    fi
  done
  return "${status}"
}

run_md_and_analysis() {
  local mae="$1"
  local gpu="$2"
  local md_dir

  log "Starting ${mae} on GPU ${gpu}."
  export LD_LIBRARY_PATH="/data1/home/huangshengjie/miniforge3/envs/md/lib:${LD_LIBRARY_PATH:-}"

  cd "${WORKDIR}"
  run_or_print env CUDA_VISIBLE_DEVICES="${gpu}" "${AUTOMD_CMD[@]:0:1}" -i "${mae}" "${AUTOMD_CMD[@]:1}"

  md_dir="$(latest_md_dir_for "${mae}")"
  if [[ "${DRY_RUN}" == true && -z "${md_dir}" ]]; then
    md_dir="${WORKDIR}/${mae%.mae}-XXXX-md"
  fi
  [[ -n "${md_dir}" ]] || {
    log "ERROR: MD directory not found for ${mae}."
    return 1
  }

  log "Running analysis in ${md_dir}."
  cd "${md_dir}" 2>/dev/null || {
    [[ "${DRY_RUN}" == true ]] || return 1
  }
  run_shell_or_print "${AUTOTRJ_SHELL_CMD}"
  run_shell_or_print "\"${SCHRODINGER:-/data1/home/huangshengjie/software/Schrodinger/2023-4}/run\" event_analysis.py report *.eaf -data -plots -data_dir analysis"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --workdir)
        WORKDIR="$(cd "$2" && pwd)"
        shift 2
        ;;
      --list)
        PENDING_LIST="$2"
        shift 2
        ;;
      --completed)
        COMPLETED_LIST="$2"
        shift 2
        ;;
      --failed)
        FAILED_LIST="$2"
        shift 2
        ;;
      --gpu)
        GPU_ID="$2"
        shift 2
        ;;
      --gpus)
        GPU_IDS="$2"
        shift 2
        ;;
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      --sleep)
        SLEEP_SECONDS="$2"
        shift 2
        ;;
      --gpu-stable-checks)
        GPU_STABLE_CHECKS="$2"
        shift 2
        ;;
      --gpu-check-interval)
        GPU_CHECK_INTERVAL="$2"
        shift 2
        ;;
      --max-free-util)
        MAX_FREE_UTIL="$2"
        shift 2
        ;;
      --max-free-mem-mb)
        MAX_FREE_MEM_MB="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  if [[ -n "${GPU_ID}" && -n "${GPU_IDS}" ]]; then
    echo "Use either --gpu or --gpus, not both." >&2
    exit 2
  fi

  [[ -f "${PENDING_LIST}" ]] || {
    echo "Pending list not found: ${PENDING_LIST}" >&2
    exit 1
  }

  mkdir -p "$(dirname "${COMPLETED_LIST}")" "$(dirname "${FAILED_LIST}")" "$(dirname "${LOG_FILE}")"
  touch "${COMPLETED_LIST}" "${FAILED_LIST}" "${LOG_FILE}"
  LOCK_FILE="${WORKDIR}/.run_serial_md.lock"
  CLAIMED_LIST="$(mktemp "${WORKDIR}/.run_serial_md.claimed.XXXXXX")"
  trap 'rm -f "${CLAIMED_LIST}"' EXIT

  if [[ -n "${GPU_IDS}" ]]; then
    run_multi_gpu_workers
    return $?
  fi

  local mae gpu
  while IFS= read -r mae || [[ -n "${mae}" ]]; do
    mae="${mae%%#*}"
    mae="$(awk '{$1=$1; print}' <<< "${mae}")"
    [[ -n "${mae}" ]] || continue

    if is_listed "${mae}" "${COMPLETED_LIST}"; then
      log "Skipping completed ${mae}."
      continue
    fi
    if has_existing_md_dir "${mae}"; then
      log "Skipping ${mae}; existing MD directory found."
      continue
    fi
    if [[ ! -f "${WORKDIR}/${mae}" ]]; then
      log "ERROR: Missing input ${mae}."
      record_status "${FAILED_LIST}" "${mae}"
      continue
    fi

    if [[ "${DRY_RUN}" == true && -n "${GPU_ID}" ]]; then
      gpu="${GPU_ID}"
    else
      gpu="$(wait_for_free_gpu)"
    fi

    if run_md_and_analysis "${mae}" "${gpu}"; then
      record_status "${COMPLETED_LIST}" "${mae}"
      log "Completed ${mae}."
    else
      record_status "${FAILED_LIST}" "${mae}"
      log "FAILED ${mae}; stopping serial run."
      exit 1
    fi
  done < "${PENDING_LIST}"
}

main "$@"
