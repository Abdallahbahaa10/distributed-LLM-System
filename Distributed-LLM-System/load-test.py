"""
CSE354 Distributed LLM System - Load Test
Tests all 3 load balancing strategies with concurrent users.
Run: python3 load_test.py
"""

import threading
import requests
import time
import statistics
from collections import defaultdict

# ── CONFIG ────────────────────────────────────────────────────────────────────
LB_IP          = "3.231.37.113"        # NGINX load balancer (public IP)
MASTER_IP      = "172.31.27.118"       # Master node (private IP, internal use)
BASE_URL       = f"http://{LB_IP}"     # All requests go through NGINX

WORKER_IPS     = [
    "172.31.17.87",
    "172.31.25.128",
    "172.31.19.47",
]

TIMEOUT        = 120    # seconds per request
WAVE_COOLDOWN  = 5     # seconds between waves

# ── WAVE CONFIGURATION ────────────────────────────────────────────────────────
# Each wave: (num_users, max_concurrent)
# 
# The user will be prompted to choose the strategy for each wave interactively.
# Available strategies:
#   1. round_robin        : Distributes requests sequentially (Worker 0→1→2→0...)
#   2. least_connections  : Routes to worker with fewest active connections
#   3. load_aware         : Routes to worker with lowest CPU/Memory usage
# ──────────────────────────────────────────────────────────────────────────────

WAVES = [
    # (num_users, max_concurrent)
    (10, 5),
    (20, 5),
    (30, 5),
    (50, 5),
    (100, 5),
    (100, 5),
    (100, 5),
    (100, 5),
]

QUERIES = [
    "What is distributed computing?",
    "Explain load balancing strategies.",
    "How does fault tolerance work?",
    "What is retrieval augmented generation?",
    "How do GPU clusters accelerate AI?",
    "What is round robin scheduling?",
    "Explain the role of a master node.",
    "What is a vector database?",
    "How does heartbeat detection work?",
    "What metrics matter in distributed systems?",
    "Explain horizontal scaling.",
    "What is the difference between latency and throughput?",
    "How does ChromaDB store embeddings?",
    "What is NGINX used for?",
    "Explain concurrent request handling.",
]

# ── HEALTH CHECK ──────────────────────────────────────────────────────────────
def check_health():
    print(f"\n{'='*60}")
    print(f"  SYSTEM HEALTH CHECK")
    print(f"{'='*60}")

    all_ok = True

    # 1. Check NGINX
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code == 200:
            print(f"  [✓] NGINX is UP at {LB_IP}")
        else:
            print(f"  [✗] NGINX returned HTTP {r.status_code}")
            all_ok = False
    except Exception as e:
        print(f"  [✗] NGINX unreachable: {e}")
        all_ok = False

    # 2. Check Master + Workers via /status
    try:
        r = requests.get(f"{BASE_URL}/status", timeout=5)
        if r.status_code == 200:
            data = r.json()
            workers = data.get("workers", [])
            alive   = [w for w in workers if w.get("alive")]
            strategy = data.get("strategy", "unknown")
            print(f"  [✓] Master is UP")
            print(f"  [✓] Strategy: {strategy}")
            print(f"  [✓] Workers alive: {len(alive)}/{len(workers)}")
            for w in workers:
                status = "✓" if w.get("alive") else "✗"
                print(f"       [{status}] Worker {w['id']} — {w['url']} | "
                      f"CPU: {w.get('cpu', 0)}% | MEM: {w.get('memory', 0)}% | "
                      f"Active: {w.get('active', 0)}")
            if len(alive) == 0:
                print(f"  [✗] No workers alive — aborting")
                all_ok = False
        else:
            print(f"  [✗] Master /status returned HTTP {r.status_code}")
            all_ok = False
    except Exception as e:
        print(f"  [✗] Master unreachable: {e}")
        all_ok = False

    return all_ok

# ── USER INPUT FOR STRATEGY ──────────────────────────────────────────────────
def prompt_for_strategy(wave_num, num_users, max_concurrent):
    """Prompt user to choose load balancing strategy for this wave"""
    print(f"\n{'='*60}")
    print(f"  WAVE {wave_num} CONFIGURATION")
    print(f"{'='*60}")
    print(f"  Users: {num_users}")
    print(f"  Max Concurrent: {max_concurrent}")
    print(f"\n  Choose Load Balancing Strategy:")
    print(f"  1. Round Robin        - Sequential distribution")
    print(f"  2. Least Connections  - Routes to least busy worker")
    print(f"  3. Load Aware         - Routes based on CPU/Memory")
    print(f"{'='*60}")
    
    strategy_map = {
        "1": "round_robin",
        "2": "least_connections",
        "3": "load_aware",
        "rr": "round_robin",
        "lc": "least_connections",
        "la": "load_aware",
        "round_robin": "round_robin",
        "least_connections": "least_connections",
        "load_aware": "load_aware",
    }
    
    while True:
        try:
            choice = input("\n  Enter choice (1/2/3 or rr/lc/la): ").strip().lower()
            
            if choice in strategy_map:
                strategy = strategy_map[choice]
                print(f"\n  ✓ Selected: {strategy.upper().replace('_', ' ')}")
                return strategy
            else:
                print(f"  ✗ Invalid choice. Please enter 1, 2, or 3")
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  ⚠ Test cancelled by user")
            exit(0)

# ── STRATEGY SWITCHER ─────────────────────────────────────────────────────────
def switch_strategy(strategy):
    """Switch load balancing strategy on the master scheduler"""
    try:
        # Try through load balancer first
        r = requests.post(f"{BASE_URL}/strategy?strategy={strategy}", timeout=5)
        if r.status_code == 200:
            print(f"\n  [→] Strategy switched to: {strategy.upper()}")
            time.sleep(1)  # Give master time to apply the change
            return True
        else:
            print(f"\n  [!] Strategy switch failed: {r.text}")
            return False
    except Exception as e:
        # Fallback: try direct connection to master
        try:
            r = requests.post(f"http://{MASTER_IP}:8000/strategy?strategy={strategy}", timeout=5)
            if r.status_code == 200:
                print(f"\n  [→] Strategy switched to: {strategy.upper()} (via master)")
                time.sleep(1)
                return True
        except:
            pass
        print(f"\n  [!] Strategy switch error: {e}")
        return False

# ── SINGLE USER THREAD ────────────────────────────────────────────────────────
def user_thread(user_id, wave_num, semaphore, results, lock):
    """
    Simulates one user sending one request.
    Semaphore controls max concurrent users.
    """
    query = QUERIES[user_id % len(QUERIES)]

    with semaphore:
        start = time.time()
        try:
            r = requests.post(
                f"{BASE_URL}/query",
                json={"id": user_id, "query": query},
                timeout=TIMEOUT,
                headers={"Content-Type": "application/json"}
            )
            latency = time.time() - start

            if r.status_code == 200:
                data   = r.json()
                error  = data.get("error")
                result = data.get("result", "")

                if error:
                    print(f"  [W{wave_num}] User {user_id:4d} | ⚠  {latency:.1f}s | ERROR: {str(error)[:50]}")
                    with lock:
                        results.append({"wave": wave_num, "id": user_id, "latency": latency, "ok": False, "error": str(error)})
                else:
                    snippet = result[:60].replace("\n", " ")
                    print(f"  [W{wave_num}] User {user_id:4d} | ✓  {latency:.1f}s | {snippet}...")
                    with lock:
                        results.append({"wave": wave_num, "id": user_id, "latency": latency, "ok": True, "error": None})
            else:
                print(f"  [W{wave_num}] User {user_id:4d} | ✗  HTTP {r.status_code}")
                with lock:
                    results.append({"wave": wave_num, "id": user_id, "latency": time.time() - start, "ok": False, "error": f"HTTP {r.status_code}"})

        except requests.exceptions.Timeout:
            latency = time.time() - start
            print(f"  [W{wave_num}] User {user_id:4d} | ⏱  TIMEOUT after {TIMEOUT}s")
            with lock:
                results.append({"wave": wave_num, "id": user_id, "latency": latency, "ok": False, "error": "timeout"})

        except Exception as e:
            latency = time.time() - start
            print(f"  [W{wave_num}] User {user_id:4d} | ✗  {str(e)[:60]}")
            with lock:
                results.append({"wave": wave_num, "id": user_id, "latency": latency, "ok": False, "error": str(e)})

# ── RUN ONE WAVE ──────────────────────────────────────────────────────────────
def run_wave(wave_num, num_users, max_concurrent, all_results, lock):
    # Prompt user to choose strategy for this wave
    strategy = prompt_for_strategy(wave_num, num_users, max_concurrent)
    
    print(f"\n{'='*60}")
    print(f"  WAVE {wave_num} | {num_users} USERS | MAX CONCURRENT: {max_concurrent} | STRATEGY: {strategy.upper()}")
    print(f"{'='*60}\n")

    # Automatically switch to the specified strategy for this wave
    if not switch_strategy(strategy):
        print(f"  [⚠] Warning: Could not switch strategy, continuing with current strategy...")
    
    time.sleep(2)  # Let strategy settle and give user time to see the change

    semaphore  = threading.Semaphore(max_concurrent)
    wave_start = time.time()

    # Launch all user threads at once — semaphore controls concurrency
    threads = [
        threading.Thread(
            target=user_thread,
            args=(user_id, wave_num, semaphore, all_results, lock),
            daemon=True
        )
        for user_id in range(num_users)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ── Wave stats ────────────────────────────────────────────────────────────
    wave_time    = time.time() - wave_start
    wave_results = [r for r in all_results if r["wave"] == wave_num]
    success      = [r for r in wave_results if r["ok"]]
    failed       = [r for r in wave_results if not r["ok"]]
    latencies    = [r["latency"] for r in success]

    avg_lat      = statistics.mean(latencies)     if latencies else 0
    median_lat   = statistics.median(latencies)   if latencies else 0
    p95_lat      = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 2 else (latencies[0] if latencies else 0)
    min_lat      = min(latencies)                 if latencies else 0
    max_lat      = max(latencies)                 if latencies else 0
    throughput   = num_users / wave_time          if wave_time > 0 else 0
    success_rate = len(success) / num_users * 100

    # Error breakdown
    error_counts = defaultdict(int)
    for r in failed:
        error_counts[r.get("error", "unknown")] += 1

    print(f"\n  ── WAVE {wave_num} SUMMARY ({strategy.upper()}) ──────────────────────")
    print(f"  ✓  Success      : {len(success)}/{num_users} ({success_rate:.1f}%)")
    print(f"  ✗  Failed       : {len(failed)}/{num_users}")
    if error_counts:
        for err, count in error_counts.items():
            print(f"       • {err}: {count}x")
    print(f"  ⏱  Total time   : {wave_time:.1f}s")
    print(f"  🚀 Throughput   : {throughput:.2f} req/s")
    print(f"  📊 Avg latency  : {avg_lat:.1f}s")
    print(f"  📊 Median lat   : {median_lat:.1f}s")
    print(f"  📊 P95 latency  : {p95_lat:.1f}s")
    print(f"  📉 Min latency  : {min_lat:.1f}s")
    print(f"  📈 Max latency  : {max_lat:.1f}s")

    return {
        "wave":         wave_num,
        "strategy":     strategy,
        "users":        num_users,
        "concurrent":   max_concurrent,
        "success":      len(success),
        "failed":       len(failed),
        "success_rate": round(success_rate, 1),
        "wave_time":    round(wave_time, 1),
        "throughput":   round(throughput, 2),
        "avg_latency":  round(avg_lat, 1),
        "median_lat":   round(median_lat, 1),
        "p95_latency":  round(p95_lat, 1),
        "min_latency":  round(min_lat, 1),
        "max_latency":  round(max_lat, 1),
    }

# ── WORKER STATUS SNAPSHOT ────────────────────────────────────────────────────
def print_worker_snapshot():
    try:
        r = requests.get(f"{BASE_URL}/status", timeout=5)
        if r.status_code == 200:
            data    = r.json()
            workers = data.get("workers", [])
            print(f"\n  [Worker Snapshot]")
            for w in workers:
                status = "ONLINE" if w.get("alive") else "OFFLINE"
                print(f"    Worker {w['id']} | {status} | "
                      f"CPU: {w.get('cpu', 0):5.1f}% | "
                      f"MEM: {w.get('memory', 0):5.1f}% | "
                      f"Active: {w.get('active', 0):3d} | "
                      f"Total: {w.get('total_requests', 0):4d}")
    except:
        pass

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("  CSE354 — DISTRIBUTED LLM SYSTEM LOAD TEST")
    print(f"  NGINX LB  : {LB_IP}")
    print(f"  Master    : {MASTER_IP}:8000")
    print(f"  Workers   : {', '.join(WORKER_IPS)}")
    print(f"  Waves     : {len(WAVES)}")
    print(f"  Timeout   : {TIMEOUT}s/request")
    print("="*60)

    if not check_health():
        print("\n  ✗ Health check failed. Make sure all VMs are running:")
        print("    • Master  : python3 scheduler_server.py")
        print("    • Worker 0: python3 worker_server.py 8001")
        print("    • Worker 1: python3 worker_server.py 8001")
        print("    • Worker 2: python3 worker_server.py 8001")
        print("    • NGINX   : sudo systemctl start nginx")
        return

    print("\n  ✓ System healthy — starting load test\n")

    all_results    = []
    lock           = threading.Lock()
    wave_summaries = []

    for i, (num_users, max_concurrent) in enumerate(WAVES, 1):
        summary = run_wave(i, num_users, max_concurrent, all_results, lock)
        wave_summaries.append(summary)
        print_worker_snapshot()

        if i < len(WAVES):
            print(f"\n  ⏳ Cooling down {WAVE_COOLDOWN}s before next wave...")
            time.sleep(WAVE_COOLDOWN)

    # ── Final summary table ───────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print("  FINAL LOAD TEST SUMMARY")
    print(f"{'='*80}")
    print(f"  {'#':>2} | {'Strategy':<20} | {'Users':>5} | {'Conc':>4} | "
          f"{'OK%':>5} | {'req/s':>6} | {'Avg':>6} | {'P95':>6} | {'Max':>6}")
    print(f"  {'-'*78}")
    for s in wave_summaries:
        print(
            f"  {s['wave']:>2} | "
            f"{s['strategy']:<20} | "
            f"{s['users']:>5} | "
            f"{s['concurrent']:>4} | "
            f"{s['success_rate']:>5.1f} | "
            f"{s['throughput']:>6.2f} | "
            f"{s['avg_latency']:>5.1f}s | "
            f"{s['p95_latency']:>5.1f}s | "
            f"{s['max_latency']:>5.1f}s"
        )
    print(f"{'='*80}\n")

    # Overall stats
    total_req = sum(s["users"]   for s in wave_summaries)
    total_ok  = sum(s["success"] for s in wave_summaries)
    
    # Strategy comparison
    print(f"\n  ── STRATEGY COMPARISON ──────────────────────────────────────")
    strategy_stats = {}
    for s in wave_summaries:
        strat = s['strategy']
        if strat not in strategy_stats:
            strategy_stats[strat] = {'waves': [], 'total_users': 0, 'total_success': 0}
        strategy_stats[strat]['waves'].append(s)
        strategy_stats[strat]['total_users'] += s['users']
        strategy_stats[strat]['total_success'] += s['success']
    
    for strat, data in strategy_stats.items():
        waves = data['waves']
        avg_success_rate = sum(w['success_rate'] for w in waves) / len(waves)
        avg_throughput = sum(w['throughput'] for w in waves) / len(waves)
        avg_latency = sum(w['avg_latency'] for w in waves) / len(waves)
        
        print(f"\n  {strat.upper()}:")
        print(f"    • Waves tested: {len(waves)}")
        print(f"    • Avg success rate: {avg_success_rate:.1f}%")
        print(f"    • Avg throughput: {avg_throughput:.2f} req/s")
        print(f"    • Avg latency: {avg_latency:.1f}s")
    
    print(f"\n  ─────────────────────────────────────────────────────────────")
    print(f"  Overall success rate : {total_ok}/{total_req} ({total_ok/total_req*100:.1f}%)")
    print(f"  Dashboard            : http://{LB_IP}/dashboard")
    print(f"  Master status        : http://{LB_IP}/status\n")

if __name__ == "__main__":
    main()