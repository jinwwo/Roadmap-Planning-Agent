#!/bin/bash
set -e

IMAGE="nvcr.io/nvidia/pytorch:23.05-py3"
CONTAINER_NAME="sieon-roadmap"
HOST_DIR="/home/sieon/26-tech-roadmap"
CONTAINER_DIR="/workspace/26-tech-roadmap"
GPU_DEVICES="1,2"

mkdir -p "$HOST_DIR"

echo "[1/3] Pulling Docker image..."
docker pull "$IMAGE"

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "[INFO] Existing container found: $CONTAINER_NAME"
    echo "[INFO] Removing existing container to apply GPU setting..."
    docker rm -f "$CONTAINER_NAME"
fi

echo "[2/3] Creating container with GPU devices: $GPU_DEVICES"
echo "[3/3] Starting container..."

docker run --gpus "\"device=${GPU_DEVICES}\"" -it \
    --name "$CONTAINER_NAME" \
    --ipc=host \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    -v "$HOST_DIR:$CONTAINER_DIR" \
    -w "$CONTAINER_DIR" \
    "$IMAGE" \
    bash