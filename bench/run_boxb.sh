#!/usr/bin/env bash
# Stock protocol, box-b (R9700). Runs ON box-b (copied to /tmp there). Read-only. Junction abort 100 C.
# NOTE ON MODEL TAGS: the `-r9700` suffix is a LOCAL alias on this box that pins the model to the
# discrete card. It is not a public tag. Substitute your own tag for the same weights; the CSV and
# the README report the canonical names (gemma4:26b-a4b-it-qat, qwen3.8:27b). The raw JSONL keeps
# the alias because that is literally what was requested during the run.
# PATHS: this script is preserved as it ran, from a staging directory. To re-run it from this repo,
# point --prompt-file at bench/prompts/ and --out at a writable path of your choosing.
set -uo pipefail
cd /tmp
B="python3 /tmp/gpu_bench.py --backend amd --sysfs-card card0 --host ${OLLAMA_ENDPOINT:?Set OLLAMA_ENDPOINT to the local ollama endpoint} --abort-temp 100 --repeats 5 --warmup 1"
$B --model gemma4:26b-a4b-it-qat-r9700 --label B-W1-moe-decode  --prompt-file /tmp/short.txt  --gen 256 --out /tmp/boxb_v2.jsonl || echo "W1 rc=$?"
$B --model qwen3.8:27b-r9700            --label B-W2-dense-decode --prompt-file /tmp/short.txt  --gen 256 --out /tmp/boxb_v2.jsonl || echo "W2 rc=$?"
$B --model qwen3.8:27b-r9700            --label B-W3-dense-prefill8k --prompt-file /tmp/long8k.txt --gen 32 --nonce --out /tmp/boxb_v2.jsonl || echo "W3 rc=$?"
$B --model gemma4:26b-a4b-it-qat-r9700 --label B-W3-moe-prefill8k --prompt-file /tmp/long8k.txt --gen 32 --nonce --out /tmp/boxb_v2.jsonl || echo "W3m rc=$?"
$B --model qwen3.8:27b-r9700            --label B-SUSTAIN-dense-15min --prompt-file /tmp/short.txt --gen 512 --repeats 1 --warmup 0 --sustain-minutes 15 --out /tmp/boxb_v2.jsonl || echo "SUSTAIN rc=$?"
echo "BOXB DONE $(date '+%H:%M:%S')"
