#!/bin/bash
# scripts/download-models.sh
# Download LLM models for vLLM inference
set -e

MODEL_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
mkdir -p "$MODEL_DIR"

download_model() {
    local hf_name="$1"
    local local_name="$2"
    local size_hint="$3"
    local target_dir="${MODEL_DIR}/${local_name}"

    if [ -d "$target_dir" ] && [ "$(ls -A "$target_dir" 2>/dev/null)" ]; then
        echo "[OK] ${local_name} already exists at ${target_dir}"
        return 0
    fi

    echo "Downloading ${hf_name} to ${target_dir}..."
    echo "This requires ~${size_hint} of disk space and may take a while."

    if command -v huggingface-cli &> /dev/null; then
        huggingface-cli download "$hf_name" --local-dir "$target_dir"
    elif command -v git &> /dev/null && command -v git-lfs &> /dev/null; then
        git lfs install
        git clone "https://huggingface.co/${hf_name}" "$target_dir"
    else
        echo "Error: Neither huggingface-cli nor git-lfs found."
        echo "Install one of:"
        echo "  pip install huggingface_hub[cli]"
        echo "  apt install git-lfs && git lfs install"
        exit 1
    fi

    echo "Download complete: ${target_dir}"
}

# Main reasoning model (QwQ-32B-AWQ for vllm service)
download_model "Qwen/Qwen3-32B-AWQ" "Qwen3-32B-AWQ" "18GB"

# Fast classification model (Qwen3.5-9B-AWQ for vllm-fast service)
download_model "cyankiwi/Qwen3.5-9B-AWQ-4bit" "Qwen3.5-9B-AWQ" "6GB"

echo ""
echo "All models downloaded successfully."
