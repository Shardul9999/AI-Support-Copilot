"""Export and quantize Cross-Encoder model to ONNX INT8 format.

Usage:
    ./venv/bin/python -m scripts.export_reranker_onnx

Generates:
    models/reranker_int8.onnx (~22 MB, 3x faster inference, ~40 MB RAM)
"""
import os
import shutil
import time
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
ONNX_FP32_PATH = os.path.join(OUTPUT_DIR, "reranker.onnx")
ONNX_INT8_PATH = os.path.join(OUTPUT_DIR, "reranker_int8.onnx")


def export_and_quantize():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"1. Loading PyTorch model [{MODEL_NAME}]...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    model.eval()

    print(f"2. Exporting to FP32 ONNX -> {ONNX_FP32_PATH}...")
    dummy_input = tokenizer("query", "passage context", return_tensors="pt")
    torch.onnx.export(
        model,
        (dummy_input["input_ids"], dummy_input["attention_mask"], dummy_input["token_type_ids"]),
        ONNX_FP32_PATH,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch_size", 1: "seq_len"},
            "attention_mask": {0: "batch_size", 1: "seq_len"},
            "token_type_ids": {0: "batch_size", 1: "seq_len"},
            "logits": {0: "batch_size"},
        },
        opset_version=14,
        dynamo=False,
    )

    print(f"3. Quantizing to INT8 -> {ONNX_INT8_PATH}...")
    from onnxruntime.quantization import quantize_dynamic, QuantType

    quantize_dynamic(
        ONNX_FP32_PATH,
        ONNX_INT8_PATH,
        weight_type=QuantType.QInt8,
    )

    # Clean up intermediate unquantized model to save disk space
    if os.path.exists(ONNX_FP32_PATH):
        os.remove(ONNX_FP32_PATH)
    extra_data = ONNX_FP32_PATH + ".data"
    if os.path.exists(extra_data):
        os.remove(extra_data)

    size_mb = os.path.getsize(ONNX_INT8_PATH) / (1024 * 1024)
    print(f"✅ Successfully created {ONNX_INT8_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    export_and_quantize()
