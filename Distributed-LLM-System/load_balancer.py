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
    
    location /dashboard {
    proxy_pass http://172.31.27.118:8000/dashboard;
    proxy_set_header Host $host;
    }

    location /health {
        return 200 "Load Balancer OK\n";
        add_header Content-Type text/plain;
    }
}
EOF