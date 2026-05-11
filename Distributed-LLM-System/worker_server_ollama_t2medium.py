cat > ~/project/worker_server.py << 'EOF'
import time
import sys
import os
import threading
import requests as req
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import psutil

sys.path.insert(0, '/home/ubuntu/project')
from rag.retriever import retrieve_context

app = FastAPI()

# Semaphore for t2.medium (4GB RAM) - can handle 4 concurrent
OLLAMA_SEMAPHORE = threading.Semaphore(5)

worker_start_time = time.time()
total_requests = 0
total_errors = 0
stats_lock = threading.Lock()

def get_metrics():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        proc_mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        return {"cpu": round(cpu, 1), "memory": round(mem, 1), "process_memory_mb": round(proc_mem, 1)}
    except:
        return {"cpu": 0, "memory": 0, "process_memory_mb": 0}

def run_llm(query: str, context: str, retries: int = 3) -> str:
    prompt = f"Context:\n{context}\n\nAnswer this question in under 80 words: {query}"
    
    with OLLAMA_SEMAPHORE:
        for attempt in range(1, retries + 1):
            try:
                resp = req.post(
                    "http://localhost:11434/api/generate",
                    json={"model": "qwen2:0.5b", "prompt": prompt, "stream": False},
                    timeout=180
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    if "response" not in data:
                        return "LLM error: unexpected response format"
                    return data["response"].strip()
                
                elif resp.status_code == 500:
                    wait = attempt * 2
                    print(f"[Worker] Ollama HTTP 500 (attempt {attempt}/{retries}), retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                
                else:
                    return f"LLM error: HTTP {resp.status_code}"
            
            except req.exceptions.Timeout:
                return "LLM error: Ollama timed out"
            except req.exceptions.ConnectionError:
                return "LLM error: Cannot connect to Ollama"
            except Exception as e:
                return f"LLM error: {str(e)[:100]}"
        
        return "LLM error: Ollama kept returning HTTP 500 after retries"

class InferenceRequest(BaseModel):
    id: int
    query: str

@app.post("/process")
def process(request: InferenceRequest):
    global total_requests, total_errors
    start = time.time()
    
    try:
        context = retrieve_context(request.query)
        result = run_llm(request.query, context)
        latency = time.time() - start
        
        with stats_lock:
            total_requests += 1
            if result.startswith("LLM error:"):
                total_errors += 1
        
        print(f"[Worker] Request {request.id} done in {latency:.2f}s")
        return {"id": request.id, "result": result, "latency": latency}
    
    except Exception as e:
        latency = time.time() - start
        with stats_lock:
            total_requests += 1
            total_errors += 1
        
        print(f"[Worker] Request {request.id} FAILED: {e}")
        return {"id": request.id, "result": f"Worker error: {str(e)[:100]}", "latency": latency}

@app.get("/health")
def health():
    m = get_metrics()
    uptime = time.time() - worker_start_time
    
    return {
        "status": "alive",
        "cpu": m["cpu"],
        "memory": m["memory"],
        "process_memory_mb": m["process_memory_mb"],
        "uptime_seconds": round(uptime, 2),
        "total_requests": total_requests,
        "total_errors": total_errors,
        "ollama_slots_free": OLLAMA_SEMAPHORE._value,
    }

@app.get("/")
def root():
    return {
        "service": "LLM Worker Node",
        "model": "qwen2:0.5b (Ollama - Self-Hosted)",
        "concurrency": 4,
        "endpoints": {
            "POST /process": "Process inference request",
            "GET  /health": "Health check with metrics",
        }
    }

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    
    print("=" * 60)
    print(f"[Worker] Starting on port {port}")
    print(f"[Worker] Model: qwen2:0.5b (Ollama - Self-Hosted)")
    print(f"[Worker] Concurrency: 4 (t2.medium with 4GB RAM)")
    print(f"[Worker] NO RATE LIMITS - Unlimited requests!")
    print("=" * 60)
    
    uvicorn.run(app, host="0.0.0.0", port=port)
EOF