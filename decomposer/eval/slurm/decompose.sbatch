#!/usr/bin/env bash
#SBATCH --job-name={{ job_name }}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task={{ gpus }}
{% if gpus == 8 %}
#SBATCH --exclusive
{% else %}
#SBATCH --cpus-per-gpu={{ cpus_per_gpu }}
#SBATCH --mem-per-gpu=120G
{% endif %}
{% if partition %}
#SBATCH --partition={{ partition }}
{% endif %}
{% if slurm_resume %}
#SBATCH --requeue
{% endif %}
{% if qos %}
#SBATCH --qos={{ qos }}
{% endif %}
{% if nice is not none %}
#SBATCH --nice={{ nice }}
{% endif %}
{% if mail_type %}
#SBATCH --mail-type={{ mail_type }}
{% endif %}
{% if mail_user %}
#SBATCH --mail-user={{ mail_user }}
{% endif %}
{% if time_limit %}
#SBATCH --time={{ time_limit }}
{% endif %}
{% if account %}
#SBATCH --account={{ account }}
{% endif %}
#SBATCH --export=ALL
#SBATCH --output="{{ output_dir }}/slurm/job_decompose_%A_%a.log"
#SBATCH --error="{{ output_dir }}/slurm/job_decompose_%A_%a.log"

set -euo pipefail
mkdir -p "{{ output_dir }}/slurm"

TASK_ID="${SLURM_ARRAY_TASK_ID}"
SHARD_FILE="{{ shard_dir }}/shard_$(printf '%03d' "${TASK_ID}").jsonl"
SHARD_OUT="{{ out_dir }}/shard_$(printf '%03d' "${TASK_ID}").jsonl"
SHARD_MARKER="{{ out_dir }}/shard_$(printf '%03d' "${TASK_ID}").done"

mkdir -p "{{ out_dir }}"

if [ -f "${SHARD_MARKER}" ]; then
    echo "Shard ${TASK_ID} already done, skipping."
    exit 0
fi

if [ ! -f "${SHARD_FILE}" ]; then
    echo "Shard ${TASK_ID} input not found, skipping."
    touch "${SHARD_MARKER}"
    exit 0
fi

export HF_HOME="{{ hf_home }}"
export SLURM_LOG_DIR="{{ output_dir }}/slurm"
source "{{ slurm_dir }}/_vllm_start.sh" "{{ model }}" "{{ gpus }}" "{{ vllm_image }}"

amfv-eval decompose \
    --config "{{ config }}" \
    --step "{{ step }}" \
    --input "${SHARD_FILE}" \
    --out "${SHARD_OUT}" \
    --base-url "${VLLM_BASE_URL}"

touch "${SHARD_MARKER}"
