#!/usr/bin/env bash
# Stock protocol, box-a (2x 5060 Ti). Read-only. N=5 + 1 warmup per config.
# PATHS: this script is preserved as it ran, from a staging directory. To re-run it from this repo,
# point --prompt-file at bench/prompts/ and --out at a writable path of your choosing.
set -uo pipefail
cd "$(dirname "$0")/.."
B="python3 h/gpu_bench.py --backend nvidia --host ${OLLAMA_ENDPOINT:?Set OLLAMA_ENDPOINT to the local ollama endpoint} --abort-temp 90 --repeats 5 --warmup 1"
$B --model gemma4:26b-a4b-it-qat --label A-W1-moe-decode  --prompt-file prompts/short.txt  --gen 256 --out runs/boxa_v2.jsonl || echo "W1 rc=$?"
$B --model qwen3.8:27b            --label A-W2-dense-decode --prompt-file prompts/short.txt  --gen 256 --out runs/boxa_v2.jsonl || echo "W2 rc=$?"
$B --model qwen3.8:27b            --label A-W3-dense-prefill8k --prompt-file prompts/long8k.txt --gen 32 --nonce --out runs/boxa_v2.jsonl || echo "W3 rc=$?"
$B --model gemma4:26b-a4b-it-qat --label A-W3-moe-prefill8k --prompt-file prompts/long8k.txt --gen 32 --nonce --out runs/boxa_v2.jsonl || echo "W3m rc=$?"
echo "BOXA DONE $(date '+%H:%M:%S')"
