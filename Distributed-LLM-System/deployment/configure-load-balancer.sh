#!/bin/bash
# Configure Load Balancer (run on 98.89.22.60)

echo "=========================================="
echo "🚀 CONFIGURING LOAD BALANCER"
echo "=========================================="

# Update nginx configuration to point to master
sudo tee /etc/nginx/sites-enabled/default > /dev/null << 'EOF'
upstream master_backend {
    server 172.31.27.118:8000;
}

server {
    listen 80;
    
    location / {
        proxy_pass         http://master_backend;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
    
    location /health {
        return 200 "Load Balancer OK\n";
        add_header Content-Type text/plain;
    }
}
EOF

# Test and reload nginx
sudo nginx -t
sudo systemctl reload nginx

echo "✅ Load Balancer configured!"
echo "📊 Test: curl http://98.89.22.60/health"
