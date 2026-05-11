# distributed-LLM-System
--Project Structure

```
distributed-llm-system/
├── README.md                           # This file
├── scheduler_server_enhanced.py        # Master scheduler (3 strategies)
├── worker_server_production.py         # Worker node code
├── load-test.py                        # Interactive load testing
│
├── rag/
│   ├── retriever.py                    # RAG module
│   └── simple_retriever.py             # Lightweight fallback
│
├── deployment/
   ├── configure-load-balancer.sh      # Nginx setup
   ├── deploy-production-workers.sh    # Worker deployment
   └── check-ollama-status.sh

## Getting Started

### Prerequisites
- AWS EC2 instances (1 load balancer, 1 master, 3 workers)
- Python 3.10 or higher
- Ollama installed on worker nodes
- SSH key: `cse354-key.pem`

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Abdallahbahaa10/distributed-LLM-System
   cd distributed-llm-system
   ```

2. **Deploy Load Balancer (NGINX)**
   ```bash
   ssh -i "cse354-key.pem" ubuntu@3.231.37.113
   sudo apt update
   sudo apt install nginx -y
   # Copy nginx configuration
   sudo systemctl start nginx
   ```

3. **Deploy Master Scheduler**
   ```bash
   scp -i "cse354-key.pem" scheduler_server_enhanced.py ubuntu@18.212.224.21:~/project/scheduler_server.py
   ssh -i "cse354-key.pem" ubuntu@<master_ip>
   cd ~/project
   python3 scheduler_server.py
   ```

4. **Deploy Workers**
   ```bash
   # Automated deployment to all 3 workers
   bash deploy-production-workers.sh
   
   # Or manually for each worker
   scp -i "cse354-key.pem" worker_server_production.py ubuntu@<WORKER_IP>:~/project/worker_server.py
   ssh -i "cse354-key.pem" ubuntu@<WORKER_IP>
   cd ~/project && python3 worker_server.py 8001 
   ```

5. **Verify Deployment**
   ```bash
   # Check system status
   curl http://3.231.37.113/status
   
   # Check Ollama on workers
   bash check-ollama-status.sh
   ```

6. **Access the Dashboard**
   - Open browser: http://3.231.37.113/dashboard
   - View real-time metrics and worker status

7. **Run Load Test**
   ```bash
   python load-test.py
   # Choose strategy when prompted (1/2/3)
   ```

---

## 📝 System Configuration

### Network Configuration

| Component | Public IP | Private IP | Port |
|-----------|-----------|------------|------|
| **Load Balancer** | 3.231.37.113 | 172.31.24.125 | 80 |
| **Master Scheduler** | - | 172.31.27.118 | 8000 |
| **Worker 0** | - | 172.31.17.87 | 8001 |
| **Worker 1** | - | 172.31.25.128 | 8001 |
| **Worker 2** | - | 172.31.19.47 | 8001 |


## Usage

### Switch Load Balancing Strategy

```bash
# Round Robin
curl -X POST http://3.231.37.113/strategy?strategy=round_robin

# Least Connections
curl -X POST http://3.231.37.113/strategy?strategy=least_connections

# Load Aware
curl -X POST http://3.231.37.113/strategy?strategy=load_aware
```

### Check System Status

```bash
# Get system status (JSON)
curl http://3.231.37.113/status

# Check NGINX health
curl http://3.231.37.113/health
```

### View Dashboard

Open browser: `http://3.231.37.113/dashboard`

### Run Load Test

```bash
python load-test.py
```

**Interactive prompts:**
- Type `1` or `rr` for Round Robin
- Type `2` or `lc` for Least Connections
- Type `3` or `la` for Load Aware

### Configure Test Waves

Edit `load-test.py`:

```python
WAVES = [
    # (num_users, max_concurrent)
    (10, 5),      # 10 users, 5 concurrent
    (50, 10),     # 50 users, 10 concurrent
    (100, 15),    # 100 users, 15 concurrent
]
