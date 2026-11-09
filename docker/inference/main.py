import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

MODEL_NAME = os.environ.get("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
DEVICE = os.environ.get("RERANK_DEVICE", "cpu")

app = FastAPI(title="AI Support Copilot - Dedicated Inference Service")

print(f"Loading CrossEncoder model {MODEL_NAME} on {DEVICE}...")
model = CrossEncoder(MODEL_NAME, device=DEVICE)
print("Model loaded successfully.")

class RerankRequest(BaseModel):
    query: str
    chunks: list[dict]
    top_k: int = 5

@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": MODEL_NAME}

@app.post("/rerank")
def rerank_endpoint(request: RerankRequest):
    if not request.chunks:
        return {"ranked_chunks": []}

    try:
        pairs = [(request.query, chunk["content"]) for chunk in request.chunks]
        scores = model.predict(pairs)
        ranked = sorted(zip(request.chunks, scores), key=lambda pair: pair[1], reverse=True)
        return {"ranked_chunks": [chunk for chunk, _ in ranked[: request.top_k]]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
