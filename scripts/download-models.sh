#!/bin/bash
# scripts/download-models.sh
# Download Qwen 2.5 32B AWQ model for vLLM inference
set -e

MODEL_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
MODEL_NAME="Qwen/Qwen2.5-32B-Instruct-AWQ"
TARGET_DIR="${MODEL_DIR}/Qwen2.5-32B-Instruct-AWQ"

if [ -d "$TARGET_DIR" ] && [ "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]; then
    echo "Model already exists at ${TARGET_DIR}"
    echo "Delete the directory and re-run to re-download."
    exit 0
fi

mkdir -p "$MODEL_DIR"

echo "Downloading ${MODEL_NAME} to ${TARGET_DIR}..."
echo "This requires ~18GB of disk space and may take a while."

if command -v huggingface-cli &> /dev/null; then
    huggingface-cli download "$MODEL_NAME" --local-dir "$TARGET_DIR"
elif command -v git &> /dev/null && command -v git-lfs &> /dev/null; then
    git lfs install
    git clone "https://huggingface.co/${MODEL_NAME}" "$TARGET_DIR"
else
    echo "Error: Neither huggingface-cli nor git-lfs found."
    echo "Install one of:"
    echo "  pip install huggingface_hub[cli]"
    echo "  apt install git-lfs && git lfs install"
    exit 1
fi

echo "Download complete: ${TARGET_DIR}"
