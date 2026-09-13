#!/usr/bin/env bash
#SBATCH --job-name={{ job_name }}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
{% if partition %}
#SBATCH --partition={{ partition }}
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
{% if account %}
#SBATCH --account={{ account }}
{% endif %}
#SBATCH --export=ALL
#SBATCH --output="{{ output_dir }}/slurm/job_shard_%j.log"
#SBATCH --error="{{ output_dir }}/slurm/job_shard_%j.log"

set -euo pipefail
mkdir -p "{{ output_dir }}/slurm"

amfv-eval shard \
    --input "{{ input }}" \
    --n "{{ n_shards }}" \
    --out-dir "{{ out_dir }}"

touch "{{ marker }}"
