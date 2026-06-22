# EnoX AI Backend Deployment Guide

## 1. Clone the Repository

```bash
cd /srv/

git clone https://github.com/RaselHasanSwe/enoxai_backend_langgraph.git

cd /srv/enoxai_backend_langgraph
```

---

## 2. Create Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install email-validator
```

---

## 3. Configure Environment Variables

Copy the example file:

```bash
cp .env.example .env
nano .env
```

Paste the following configuration into `.env`:

```env
# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small

# ── RAG tuning ────────────────────────────────────────────────────────────────
FAQ_DATA_PATH=data/faq.json
FAISS_INDEX_PATH=data/faiss_index
PRODUCT_FAISS_INDEX_PATH=data/product_faiss_index
PRODUCT_DATA_PATH=data/products.json
CHAT_STORE_PATH=data/enoxai.db
TOP_K_RESULTS=4
BM25_WEIGHT=0.4
SEMANTIC_WEIGHT=0.6

# ── Laravel backend ───────────────────────────────────────────────────────────
ENOX_API_URL=
ENOX_API_KEY=your-internal-key-here

# ── LangSmith Core Tracing Flags ──────────────────────────────────────────────
LANGSMITH_TRACING=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=YOUR_LANGSMITH_API_KEY
LANGSMITH_PROJECT="EnoXAI"
```

---

## 4. Test the Application

Before configuring systemd, verify that the application starts successfully:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Open in browser:

```text
http://SERVER_IP:8001
```

Press `CTRL + C` after testing.

---

## 5. Configure Systemd Service

Create a new service file:

```bash
sudo nano /etc/systemd/system/enoxai-backend.service
```

Paste:

```ini
[Unit]
Description=EnoX AI FastAPI Backend
After=network.target

[Service]
User=ukenorsia
Group=www-data
WorkingDirectory=/srv/enoxai_backend_langgraph
Environment="PATH=/srv/enoxai_backend_langgraph/venv/bin"
EnvironmentFile=/srv/enoxai_backend_langgraph/.env
ExecStart=/srv/enoxai_backend_langgraph/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## 6. Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable enoxai-backend
sudo systemctl start enoxai-backend
```

Check status:

```bash
sudo systemctl status enoxai-backend
```

View logs:

```bash
sudo journalctl -u enoxai-backend -f
```

---

## 7. Configure Nginx Reverse Proxy

Create Nginx configuration:

```bash
sudo nano /etc/nginx/sites-available/enoxai_backend
```

Paste:

```nginx
server {
    listen 80;
    server_name enoxaibe.enoxsuite.com;

    # Required for SSE streaming
    location /api/v1/chat/stream {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        chunked_transfer_encoding on;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/enoxai_backend /etc/nginx/sites-enabled/
```

Validate configuration:

```bash
sudo nginx -t
```

Reload Nginx:

```bash
sudo systemctl reload nginx
```

---

## 8. Configure SSL Certificate

Generate SSL certificate using Certbot:

```bash
sudo certbot --nginx -d enoxaibe.enoxsuite.com
```

Verify:

```text
https://enoxaibe.enoxsuite.com
```

---

## 9. Pakiza Network Access Configuration

If you are connected to the Pakiza network, configure your local hosts file.

### Windows

Open Notepad as Administrator and edit:

```text
C:\Windows\System32\drivers\etc\hosts
```

Add:

```text
172.16.61.171 enoxsuite.com
172.16.61.171 enorsiastaging.enoxsuite.com
172.16.61.171 enoxaife.enoxsuite.com
172.16.61.171 enoxaibe.enoxsuite.com
```

Flush DNS cache:

```cmd
ipconfig /flushdns
```

After that, you can access:

```text
https://enoxsuite.com
https://enorsiastaging.enoxsuite.com
https://enoxaife.enoxsuite.com
https://enoxaibe.enoxsuite.com
```

from within the Pakiza network.

---

## Troubleshooting

### Check Service Status

```bash
sudo systemctl status enoxai-backend
```

### View Logs

```bash
sudo journalctl -u enoxai-backend -f
```

### Check Port Usage

```bash
sudo ss -tulpn | grep :8001
```

### Test Nginx Configuration

```bash
sudo nginx -t
```

### Restart Services

```bash
sudo systemctl restart enoxai-backend
sudo systemctl reload nginx
```


### MAINTENANCE MODE
Open .env then 
```bash
MAINTENANCE_MODE=false | true
sudo systemctl restart enoxai-backend
```
