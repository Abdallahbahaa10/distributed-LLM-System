#!/bin/bash
# Check Ollama status on all workers

KEY="C:/Users/mohamed/Downloads/cse354-key.pem"
WORKERS=("172.31.17.87" "172.31.25.128" "172.31.19.47")
PORTS=(8000 8001 8002)

echo "=========================================="
echo "CHECKING OLLAMA STATUS"
echo "=========================================="
echo ""

for i in "${!WORKERS[@]}"; do
    WORKER_IP="${WORKERS[$i]}"
    PORT="${PORTS[$i]}"
    
    echo "Worker $i ($WORKER_IP:$PORT):"
    echo "─────────────────────────────────────────"
    
    # Check if Ollama process is running
    OLLAMA_PID=$(ssh -i "$KEY" ubuntu@${WORKER_IP} "pgrep ollama 2>/dev/null")
    if [ -n "$OLLAMA_PID" ]; then
        echo "  Ollama process: ✓ Running (PID: $OLLAMA_PID)"
    else
        echo "  Ollama process: ✗ NOT running"
    fi
    
    # Check if Ollama API responds
    OLLAMA_API=$(ssh -i "$KEY" ubuntu@${WORKER_IP} "curl -s -o /dev/null -w '%{http_code}' http://localhost:11434/api/tags 2>/dev/null")
    if [ "$OLLAMA_API" = "200" ]; then
        echo "  Ollama API:     ✓ Responding (HTTP 200)"
    else
        echo "  Ollama API:     ✗ Not responding (HTTP $OLLAMA_API)"
    fi
    
    # Check if worker can reach Ollama
    WORKER_TEST=$(ssh -i "$KEY" ubuntu@${WORKER_IP} "curl -s http://localhost:$PORT/health 2>/dev/null | grep -o '\"status\":\"alive\"'")
    if [ -n "$WORKER_TEST" ]; then
        echo "  Worker health:  ✓ Alive"
    else
        echo "  Worker health:  ✗ Not responding"
    fi
    
    # Check Ollama config
    CONFIG_EXISTS=$(ssh -i "$KEY" ubuntu@${WORKER_IP} "test -f /etc/systemd/system/ollama.service.d/override.conf && echo 'yes' || echo 'no'")
    if [ "$CONFIG_EXISTS" = "yes" ]; then
        echo "  Ollama config:  ✓ Configured"
    else
        echo "  Ollama config:  ✗ Not configured"
    fi
    
    echo ""
done

echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo ""
echo "If any worker shows Ollama as NOT running, run:"
echo "  bash start-ollama-all-workers.sh"
echo ""
