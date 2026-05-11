cat > ~/project/scheduler_server.py << 'EOF'
import time
import threading
import requests as req
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
from datetime import datetime

app = FastAPI()

# Configuration - Change this to switch strategies
LOAD_BALANCING_STRATEGY = "round_robin"  # Options: "round_robin", "least_connections", "load_aware"

WORKERS = [
    {"id": 0, "url": "http://172.31.17.87:8001", "alive": True, "active": 0, "total_requests": 0, "cpu": 0, "memory": 0},
    {"id": 1, "url": "http://172.31.25.128:8001", "alive": True, "active": 0, "total_requests": 0, "cpu": 0, "memory": 0},
    {"id": 2, "url": "http://172.31.19.47:8001", "alive": True, "active": 0, "total_requests": 0, "cpu": 0, "memory": 0},
]

rr_index = 0
lock = threading.Lock()
stats = {
    "total": 0,
    "errors": 0,
    "latency_sum": 0.0,
    "start_time": time.time(),
    "strategy": LOAD_BALANCING_STRATEGY
}

# ============================================================================
# LOAD BALANCING STRATEGIES
# ============================================================================

def get_next_worker_round_robin():
    """Round Robin: Distribute requests sequentially"""
    global rr_index
    alive = [w for w in WORKERS if w["alive"]]
    if not alive:
        raise Exception("All workers are down")
    
    with lock:
        worker = alive[rr_index % len(alive)]
        rr_index = (rr_index + 1) % len(alive)
        return worker

def get_next_worker_least_connections():
    """Least Connections: Send to worker with fewest active connections"""
    alive = [w for w in WORKERS if w["alive"]]
    if not alive:
        raise Exception("All workers are down")
    
    with lock:
        # Find minimum active connections
        min_active = min(w["active"] for w in alive)
        
        # Get all workers with minimum connections
        candidates = [w for w in alive if w["active"] == min_active]
        
        # If multiple workers have same connections, use round robin among them
        if len(candidates) > 1:
            global rr_index
            worker = candidates[rr_index % len(candidates)]
            rr_index = (rr_index + 1) % len(candidates)
        else:
            worker = candidates[0]
        
        return worker

def get_next_worker_load_aware():
    """Load Aware: Send to worker with lowest CPU/memory usage"""
    alive = [w for w in WORKERS if w["alive"]]
    if not alive:
        raise Exception("All workers are down")
    
    with lock:
        # Calculate load score (CPU + Memory) / 2
        # Lower score = less loaded
        for w in alive:
            w["load_score"] = (w["cpu"] + w["memory"]) / 2
        
        # Find worker with minimum load
        worker = min(alive, key=lambda w: w["load_score"])
        return worker

def get_next_worker():
    """Main function that calls the appropriate strategy"""
    if LOAD_BALANCING_STRATEGY == "round_robin":
        return get_next_worker_round_robin()
    elif LOAD_BALANCING_STRATEGY == "least_connections":
        return get_next_worker_least_connections()
    elif LOAD_BALANCING_STRATEGY == "load_aware":
        return get_next_worker_load_aware()
    else:
        return get_next_worker_round_robin()  # Default

# ============================================================================
# HEALTH MONITORING
# ============================================================================

def heartbeat_loop():
    """Monitor worker health and collect metrics"""
    missed = {w["id"]: 0 for w in WORKERS}
    
    while True:
        time.sleep(2)
        for w in WORKERS:
            try:
                resp = req.get(f"{w['url']}/health", timeout=2)
                if resp.status_code == 200:
                    missed[w["id"]] = 0
                    
                    # Get real metrics from worker
                    try:
                        data = resp.json()
                        w["cpu"] = data.get("cpu", 0)
                        w["memory"] = data.get("memory", 0)
                        w["process_memory_mb"] = data.get("process_memory_mb", 0)
                        w["uptime"] = data.get("uptime_seconds", 0)
                    except:
                        # Fallback: simulate metrics based on active connections
                        w["cpu"] = min(100, w["active"] * 15)
                        w["memory"] = min(100, w["active"] * 12)
                    
                    if not w["alive"]:
                        w["alive"] = True
                        print(f"[Master] Worker {w['id']} recovered")
            except:
                missed[w["id"]] += 1
                print(f"[Master] Worker {w['id']} missed heartbeat {missed[w['id']]}/3")
                if missed[w["id"]] >= 3:
                    w["alive"] = False
                    print(f"[Master] Worker {w['id']} declared FAILED")

threading.Thread(target=heartbeat_loop, daemon=True).start()

# ============================================================================
# API ENDPOINTS
# ============================================================================

class QueryRequest(BaseModel):
    id: int
    query: str

@app.post("/query")
def handle_query(request: QueryRequest):
    with lock:
        stats["total"] += 1
    
    try:
        worker = get_next_worker()
        
        # Increment active connections
        with lock:
            worker["active"] += 1
            worker["total_requests"] += 1
        
        print(f"[Master] Request {request.id} → Worker {worker['id']} (Strategy: {LOAD_BALANCING_STRATEGY})")
        
        resp = req.post(
            f"{worker['url']}/process",
            json={"id": request.id, "query": request.query},
            timeout=120
        )
        result = resp.json()
        
        with lock:
            stats["latency_sum"] += result.get("latency", 0)
            worker["active"] -= 1  # Decrement active connections
        
        return result
    
    except Exception as e:
        with lock:
            stats["errors"] += 1
            if "worker" in locals():
                worker["active"] = max(0, worker["active"] - 1)
        
        return {"id": request.id, "result": "", "latency": 0, "error": str(e)}

@app.get("/status")
def status():
    """API endpoint for status (JSON)"""
    with lock:
        total = stats["total"]
        avg = (stats["latency_sum"] / total) if total > 0 else 0
        uptime = time.time() - stats["start_time"]
        
        return {
            "total_requests": total,
            "errors": stats["errors"],
            "avg_latency": round(avg, 3),
            "uptime_seconds": round(uptime, 2),
            "strategy": LOAD_BALANCING_STRATEGY,
            "workers": WORKERS,
        }

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Web dashboard for monitoring"""
    with lock:
        total = stats["total"]
        avg = (stats["latency_sum"] / total) if total > 0 else 0
        uptime = time.time() - stats["start_time"]
        success_rate = ((total - stats["errors"]) / total * 100) if total > 0 else 0
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Distributed LLM System - Monitoring Dashboard</title>
        <meta http-equiv="refresh" content="2">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: #333;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            h1 {{
                color: white;
                text-align: center;
                margin-bottom: 30px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }}
            .stats-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .stat-card {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .stat-card h3 {{
                margin: 0 0 10px 0;
                color: #667eea;
                font-size: 14px;
                text-transform: uppercase;
            }}
            .stat-card .value {{
                font-size: 32px;
                font-weight: bold;
                color: #333;
            }}
            .stat-card .unit {{
                font-size: 14px;
                color: #666;
            }}
            .workers-section {{
                background: white;
                padding: 20px;
                border-radius: 10px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .workers-section h2 {{
                margin-top: 0;
                color: #667eea;
            }}
            .worker-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 15px;
                margin-top: 20px;
            }}
            .worker-card {{
                border: 2px solid #e0e0e0;
                padding: 15px;
                border-radius: 8px;
                position: relative;
            }}
            .worker-card.alive {{
                border-color: #4caf50;
                background: #f1f8f4;
            }}
            .worker-card.dead {{
                border-color: #f44336;
                background: #fef1f0;
            }}
            .worker-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 10px;
            }}
            .worker-id {{
                font-size: 18px;
                font-weight: bold;
            }}
            .status-badge {{
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 12px;
                font-weight: bold;
            }}
            .status-badge.alive {{
                background: #4caf50;
                color: white;
            }}
            .status-badge.dead {{
                background: #f44336;
                color: white;
            }}
            .worker-metric {{
                display: flex;
                justify-content: space-between;
                padding: 5px 0;
                border-bottom: 1px solid #e0e0e0;
            }}
            .worker-metric:last-child {{
                border-bottom: none;
            }}
            .metric-label {{
                color: #666;
                font-size: 14px;
            }}
            .metric-value {{
                font-weight: bold;
                color: #333;
            }}
            .progress-bar {{
                width: 100%;
                height: 8px;
                background: #e0e0e0;
                border-radius: 4px;
                overflow: hidden;
                margin-top: 5px;
            }}
            .progress-fill {{
                height: 100%;
                background: #667eea;
                transition: width 0.3s;
            }}
            .strategy-badge {{
                display: inline-block;
                padding: 8px 16px;
                background: #ffd700;
                color: #333;
                border-radius: 20px;
                font-weight: bold;
                margin-bottom: 20px;
            }}
            .timestamp {{
                text-align: center;
                color: white;
                margin-top: 20px;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 Distributed LLM System - Monitoring Dashboard</h1>
            
            <div style="text-align: center;">
                <span class="strategy-badge">Load Balancing: {LOAD_BALANCING_STRATEGY.upper().replace('_', ' ')}</span>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>Total Requests</h3>
                    <div class="value">{total}</div>
                </div>
                
                <div class="stat-card">
                    <h3>Success Rate</h3>
                    <div class="value">{success_rate:.1f}<span class="unit">%</span></div>
                </div>
                
                <div class="stat-card">
                    <h3>Avg Latency</h3>
                    <div class="value">{avg:.2f}<span class="unit">s</span></div>
                </div>
                
                <div class="stat-card">
                    <h3>Errors</h3>
                    <div class="value" style="color: #f44336;">{stats["errors"]}</div>
                </div>
                
                <div class="stat-card">
                    <h3>Uptime</h3>
                    <div class="value">{int(uptime // 60)}<span class="unit">m</span> {int(uptime % 60)}<span class="unit">s</span></div>
                </div>
                
                <div class="stat-card">
                    <h3>Throughput</h3>
                    <div class="value">{(total / uptime if uptime > 0 else 0):.2f}<span class="unit">req/s</span></div>
                </div>
            </div>
            
            <div class="workers-section">
                <h2>Worker Nodes Status</h2>
                <div class="worker-grid">
    """
    
    for w in WORKERS:
        status_class = "alive" if w["alive"] else "dead"
        status_text = "ONLINE" if w["alive"] else "OFFLINE"
        
        html += f"""
                    <div class="worker-card {status_class}">
                        <div class="worker-header">
                            <div class="worker-id">Worker {w['id']}</div>
                            <span class="status-badge {status_class}">{status_text}</span>
                        </div>
                        
                        <div class="worker-metric">
                            <span class="metric-label">URL:</span>
                            <span class="metric-value">{w['url']}</span>
                        </div>
                        
                        <div class="worker-metric">
                            <span class="metric-label">Active Connections:</span>
                            <span class="metric-value">{w['active']}</span>
                        </div>
                        
                        <div class="worker-metric">
                            <span class="metric-label">Total Requests:</span>
                            <span class="metric-value">{w['total_requests']}</span>
                        </div>
                        
                        <div class="worker-metric">
                            <span class="metric-label">CPU Usage:</span>
                            <span class="metric-value">{w['cpu']}%</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {w['cpu']}%; background: {'#f44336' if w['cpu'] > 80 else '#4caf50'};"></div>
                        </div>
                        
                        <div class="worker-metric">
                            <span class="metric-label">Memory Usage:</span>
                            <span class="metric-value">{w['memory']}%</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: {w['memory']}%; background: {'#f44336' if w['memory'] > 80 else '#667eea'};"></div>
                        </div>
                    </div>
        """
    
    html += f"""
                </div>
            </div>
            
            <div class="timestamp">
                Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Auto-refresh every 2 seconds
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

@app.post("/strategy")
def change_strategy(strategy: str):
    """Change load balancing strategy dynamically"""
    global LOAD_BALANCING_STRATEGY
    
    valid_strategies = ["round_robin", "least_connections", "load_aware"]
    if strategy not in valid_strategies:
        return {"error": f"Invalid strategy. Choose from: {valid_strategies}"}
    
    LOAD_BALANCING_STRATEGY = strategy
    stats["strategy"] = strategy
    print(f"[Master] Load balancing strategy changed to: {strategy}")
    
    return {"message": f"Strategy changed to {strategy}", "current_strategy": LOAD_BALANCING_STRATEGY}

if __name__ == "__main__":
    print(f"[Master] Starting scheduler on port 8000")
    print(f"[Master] Load Balancing Strategy: {LOAD_BALANCING_STRATEGY}")
    print(f"[Master] Dashboard available at: http://localhost:8000/dashboard")
    uvicorn.run(app, host="0.0.0.0", port=8000)
EOF