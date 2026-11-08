import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_model = None
_onnx_session = None
_tokenizer = None
ONNX_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "models", "reranker_int8.onnx")


def _get_onnx_session():
    """Lazy initialization of quantized INT8 ONNX Runtime inference session.
    Reduces memory from ~1GB to ~40MB and speeds up CPU inference ~3x."""
    global _onnx_session, _tokenizer
    if _onnx_session is None and os.path.exists(ONNX_MODEL_PATH):
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            _tokenizer = AutoTokenizer.from_pretrained("cross-encoder/ms-marco-MiniLM-L-6-v2")
            _onnx_session = ort.InferenceSession(ONNX_MODEL_PATH, providers=["CPUExecutionProvider"])
            logger.info(f"Loaded quantized INT8 ONNX reranker from {ONNX_MODEL_PATH}")
        except Exception as exc:
            logger.warning(f"Failed to load ONNX model: {exc}. Falling back to PyTorch CrossEncoder.")
            _onnx_session = None
    return _onnx_session, _tokenizer


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
    return _model


def _remote_rerank(service_url: str, query: str, chunks: list[dict], top_k: int) -> list[dict] | None:
    try:
        payload = json.dumps({"query": query, "chunks": chunks, "top_k": top_k}).encode("utf-8")
        req = urllib.request.Request(
            f"{service_url.rstrip('/')}/rerank",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("ranked_chunks", [])
    except Exception as exc:
        logger.warning(f"Remote rerank service ({service_url}) failed: {exc}. Falling back to local model.")
    return None


def rerank(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    """Reads query and chunk together in one pass (unlike vector/keyword search,
    which score each independently) — far more accurate, far too slow to run
    against every chunk, hence narrow-then-rerank instead of rerank-everything.

    If RERANK_SERVICE_URL is set, proxies inference to dedicated microservice,
    freeing async worker processes from PyTorch runtime memory overhead."""
    if not chunks:
        return []

    service_url = os.environ.get("RERANK_SERVICE_URL")
    if service_url:
        remote_result = _remote_rerank(service_url, query, chunks, top_k)
        if remote_result is not None:
            return remote_result

    # 1. High-efficiency quantized ONNX Runtime (3x faster, ~40MB RAM)
    onnx_session, tokenizer = _get_onnx_session()
    if onnx_session is not None and tokenizer is not None:
        contents = [c["content"] for c in chunks]
        inputs = tokenizer(
            [query] * len(chunks),
            contents,
            padding=True,
            truncation=True,
            return_tensors="np",
        )
        ort_inputs = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
            "token_type_ids": inputs["token_type_ids"],
        }
        outputs = onnx_session.run(None, ort_inputs)
        scores = outputs[0].flatten()
        ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
        return [chunk for chunk, _ in ranked[:top_k]]

    # 2. PyTorch CrossEncoder fallback
    model = _get_model()
    pairs = [(query, c["content"]) for c in chunks]
    scores = model.predict(pairs)
    ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, _ in ranked[:top_k]]
