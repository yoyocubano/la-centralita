#!/bin/bash
set -e

MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models/piper"
mkdir -p "$MODEL_DIR"

MODEL_FILE="$MODEL_DIR/es_ES-sharvard-medium.onnx"
CONFIG_FILE="$MODEL_DIR/es_ES-sharvard-medium.onnx.json"

if [ ! -f "$MODEL_FILE" ]; then
    echo "Descargando modelo de voz Piper es_ES-sharvard-medium..."
    curl -L -o "$MODEL_FILE" "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx"
else
    echo "Modelo de voz ya existe en $MODEL_FILE"
fi

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Descargando configuracion de voz..."
    curl -L -o "$CONFIG_FILE" "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx.json"
else
    echo "Configuracion de voz ya existe en $CONFIG_FILE"
fi

echo "Modelos Piper listos para La Centralita."
