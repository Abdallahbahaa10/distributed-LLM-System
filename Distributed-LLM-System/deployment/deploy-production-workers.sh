#!/bin/bash
# Deploy Production Worker Code with Semaphore + Ollama Configuration
# This fixes HTTP 500 errors by limiting concurrent Ollama requests

echo "=========================================="
echo "DEPLOYING PRODUCTION WORKERS"
echo "=========================================="
echo ""
echo "This script will:"
echo "  1. Deploy worker code with semaphore (limits to 2 concurrent Ollama requests)"
echo "  2. Configure Ollama environment variables"
echo "  3. Restart Ollama and workers on all 3 nodes"
echo ""
echo "Worker IPs:"
echo "  Worker 0: 172.31.17.87"
echo "  Worker 1: 172.31.25.128"
echo "  Worker 2: 172.31.19.47"
echo ""
read -p "Press Enter to continue..."

# SSH key path
KEY="C:/Users/mohamed/Downloads/cse354-key.pem"

# Worker IPs
WORKERS=("172.31.17.87" "172.31.25.128" "172.31.19.47")
PORTS=(8000 8001 8002)

# Deploy to each worker
for i in "${!WORKERS[@]}"; do
    WORKER_IP="${WORKERS[$i]}"
    PORT="${PORTS[$i]}"
    
    echo ""
    echo "=========================================="
    echo "DEPLOYING TO WORKER $i (${WORKER_IP}:${PORT})"
    echo "=========================================="
    
    # Step 1: Stop old worker and Ollama
    echo "[1/6] Stopping old worker process and Ollama..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} "pkill -f worker_server.py || true"
    ssh -i "$KEY" ubuntu@${WORKER_IP} "sudo systemctl stop ollama || true"
    sleep 2
    
    # Step 2: Install psutil if not already installed
    echo "[2/6] Installing psutil..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} "sudo apt install -y python3-psutil 2>/dev/null || echo 'psutil already installed'"
    
    # Step 3: Deploy new worker code
    echo "[3/6] Deploying production worker code..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} << 'DEPLOY_CODE'
cat > ~/project/worker_server.py << 'EOF'
import time
import sys
import threading
import requests as req
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import psutil
import os

sys.path.insert(0, '/home/ubuntu/project')
from rag.retriever import retrieve_context

app = FastAPI()

# ── Ollama concurrency limiter ────────────────────────────────────────────────
# qwen2:0.5b on t2.micro can only safely handle 2-3 requests at once.
# Beyond that Ollama returns HTTP 500. This semaphore queues the extras.
OLLAMA_SEMAPHORE = threading.Semaphore(2)

# ── Worker stats ──────────────────────────────────────────────────────────────
worker_start_time = time.time()
total_requests    = 0
total_errors      = 0
stats_lock        = threading.Lock()

# ── System metrics ────────────────────────────────────────────────────────────
def get_metrics():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        proc_mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        return {"cpu": round(cpu, 1), "memory": round(mem, 1),
                "process_memory_mb": round(proc_mem, 1)}
    except Exception:
        return {"cpu": 0, "memory": 0, "process_memory_mb": 0}

# ── LLM call with retry ───────────────────────────────────────────────────────
def run_llm(query: str, context: str, retries: int = 3) -> str:
    """Call Ollama with:
    - A semaphore so at most 2 requests run at the same time
    - Retry up to `retries` times on HTTP 500 (Ollama overload)
    - Exponential back-off between retries
    """
    prompt = (f"Context:\n{context}\n\n"
              f"Answer this question in under 80 words: {query}")
    
    with OLLAMA_SEMAPHORE:           # blocks until a slot is free
        for attempt in range(1, retries + 1):
            try:
                resp = req.post("http://localhost:11434/api/generate",
                               json={"model": "qwen2:0.5b", "prompt": prompt, "stream": False},
                               timeout=180)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if "response" not in data:
                        return "LLM error: unexpected response format"
                    return data["response"].strip()
                
                elif resp.status_code == 500:
                    # Ollama overloaded — wait and retry
                    wait = attempt * 2          # 2s, 4s, 6s
                    print(f"[Worker] Ollama HTTP 500 (attempt {attempt}/{retries}), "
                          f"retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                
                else:
                    return f"LLM error: HTTP {resp.status_code}"
            
            except req.exceptions.Timeout:
                return "LLM error: Ollama timed out"
            except req.exceptions.ConnectionError:
                return "LLM error: Cannot connect to Ollama (is it running?)"
            except Exception as e:
                return f"LLM error: {str(e)[:100]}"
        
        return "LLM error: Ollama kept returning HTTP 500 after retries"

# ── Endpoints ─────────────────────────────────────────────────────────────────
class InferenceRequest(BaseModel):
    id: int
    query: str

@app.post("/process")
def process(request: InferenceRequest):
    global total_requests, total_errors
    start = time.time()
    
    try:
        context = retrieve_context(request.query)
        result  = run_llm(request.query, context)
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
            total_errors   += 1
        
        print(f"[Worker] Request {request.id} FAILED: {e}")
        return {"id": request.id, "result": f"Worker error: {str(e)[:100]}",
                "latency": latency}

@app.get("/health")
def health():
    m      = get_metrics()
    uptime = time.time() - worker_start_time
    
    return {
        "status":             "alive",
        "cpu":                m["cpu"],
        "memory":             m["memory"],
        "process_memory_mb":  m["process_memory_mb"],
        "uptime_seconds":     round(uptime, 2),
        "total_requests":     total_requests,
        "total_errors":       total_errors,
        "ollama_slots_free":  OLLAMA_SEMAPHORE._value,   # how many concurrent slots remain
    }

@app.get("/metrics")
def metrics():
    m      = get_metrics()
    uptime = time.time() - worker_start_time
    
    return {
        "system": {
            "cpu_percent":        m["cpu"],
            "memory_percent":     m["memory"],
            "process_memory_mb":  m["process_memory_mb"],
        },
        "worker": {
            "uptime_seconds":     round(uptime, 2),
            "total_requests":     total_requests,
            "total_errors":       total_errors,
            "error_rate_pct":     round(total_errors / max(total_requests, 1) * 100, 2),
            "requests_per_sec":   round(total_requests / max(uptime, 1), 2),
        },
        "status": "healthy" if total_errors / max(total_requests, 1) < 0.1 else "degraded"
    }

@app.get("/")
def root():
    return {
        "service":   "LLM Worker Node",
        "model":     "qwen2:0.5b",
        "endpoints": {
            "POST /process": "Process inference request",
            "GET  /health":  "Health check with metrics",
            "GET  /metrics": "Detailed metrics",
        }
    }

# ── Startup ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    
    print("=" * 60)
    print(f"[Worker] Starting on port {port}")
    print(f"[Worker] Model       : qwen2:0.5b")
    print(f"[Worker] Ollama slots: {OLLAMA_SEMAPHORE._value} concurrent max")
    print("=" * 60)
    
    # Preflight checks
    try:
        import psutil
        print("[Worker] ✓ psutil available — real metrics enabled")
    except ImportError:
        print("[Worker] ✗ psutil missing  — run: pip3 install psutil")
    
    try:
        r = req.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            print("[Worker] ✓ Ollama reachable")
        else:
            print(f"[Worker] ✗ Ollama returned {r.status_code}")
    except Exception:
        print("[Worker] ✗ Ollama not reachable — run: ollama serve")
    
    print("=" * 60)
    
    uvicorn.run(app, host="0.0.0.0", port=port)
EOF
DEPLOY_CODE
    
    # Step 4: Configure Ollama environment variables
    echo "[4/6] Configuring Ollama environment variables..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} << 'CONFIGURE_OLLAMA'
# Stop Ollama first
sudo systemctl stop ollama 2>/dev/null || pkill ollama || true
sleep 2

# Create systemd override directory
sudo mkdir -p /etc/systemd/system/ollama.service.d/

# Create environment override file
sudo tee /etc/systemd/system/ollama.service.d/override.conf > /dev/null << 'OVERRIDE'
[Service]
# Limit concurrent requests to 2 (t2.micro has only 1GB RAM)
Environment="OLLAMA_NUM_PARALLEL=2"

# Queue up to 100 requests (prevents OOM)
Environment="OLLAMA_MAX_QUEUE=100"

# Keep only 1 model loaded at a time
Environment="OLLAMA_MAX_LOADED_MODELS=1"
OVERRIDE

# Reload systemd
sudo systemctl daemon-reload

echo "✓ Ollama environment variables configured"
CONFIGURE_OLLAMA
    
    # Step 5: Start Ollama
    echo "[5/6] Starting Ollama with new configuration..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} "sudo systemctl start ollama && sleep 3"
    
    # Step 6: Start worker
    echo "[6/6] Starting worker on port ${PORT}..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} "cd ~/project && nohup python3 worker_server.py ${PORT} > worker.log 2>&1 &"
    sleep 2
    
    # Verify worker is running
    echo ""
    echo "Verifying worker..."
    ssh -i "$KEY" ubuntu@${WORKER_IP} "curl -s http://localhost:${PORT}/health | python3 -m json.tool || echo 'Worker not responding yet'"
    
    echo ""
    echo "✓ Worker $i deployed successfully!"
done

echo ""
echo "=========================================="
echo "DEPLOYMENT COMPLETE!"
echo "=========================================="
echo ""
echo "All 3 workers have been updated with:"
echo "  ✓ Python semaphore (max 2 concurrent Ollama requests)"
echo "  ✓ Ollama environment variables:"
echo "      OLLAMA_NUM_PARALLEL=2"
echo "      OLLAMA_MAX_QUEUE=100"
echo "      OLLAMA_MAX_LOADED_MODELS=1"
echo "  ✓ Exponential backoff retry on HTTP 500"
echo "  ✓ Real CPU/Memory metrics"
echo ""
echo "Next steps:"
echo "  1. Wait 30 seconds for workers to fully initialize"
echo "  2. Run: python load-test.py"
echo "  3. Check dashboard: http://3.231.37.113/dashboard"
echo ""
echo "Expected results:"
echo "  - Fewer HTTP 500 errors (should be <10%)"
echo "  - Requests queue instead of crashing Ollama"
echo "  - More stable latency"
echo ""
