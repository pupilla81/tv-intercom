# Guida Avvio e Deploy

---

## Avvio in locale (sviluppo e test)

### 1. Installa dipendenze

```bash
# Nella cartella server/
pip install -r server/requirements.txt
```

### 2. Avvia il server

```bash
cd server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Il server è ora raggiungibile su:
- API: `http://localhost:8000`
- Documentazione API interattiva: `http://localhost:8000/docs`
- PWA Operatore: `http://localhost:8000/operator/`

### 3. Apri il pannello regia
Apri il file `client-regia/intercom-regia.jsx` nel browser o nell'app Electron.

### 4. Connetti un operatore (test su stesso PC)
Apri in un browser:
```
http://localhost:8000/operator/?cam=1
```
Per simulare più operatori apri più tab con `cam=1`, `cam=2`, ecc.

### 5. Testa il sistema
Usa l'API interattiva su `http://localhost:8000/docs` per:
- `POST /api/stt/chunk` — simula una trascrizione STT
- `POST /api/cues/fire` — scatta un cue manualmente
- `GET /api/status` — verifica lo stato del sistema

---

## Deploy su VPS (produzione)

### Requisiti VPS
- Ubuntu 22.04 LTS
- 2 vCPU, 4GB RAM (minimo)
- Porta 8000 aperta nel firewall (o 443 con HTTPS)

### Installazione

```bash
# Sul VPS
sudo apt update && sudo apt install python3-pip python3-venv git -y

git clone https://github.com/TUO-USERNAME/tv-intercom.git
cd tv-intercom

python3 -m venv venv
source venv/bin/activate
pip install -r server/requirements.txt
```

### Avvio come servizio (systemd)

```bash
sudo nano /etc/systemd/system/tv-intercom.service
```

```ini
[Unit]
Description=TV Intercom Server
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tv-intercom/server
Environment="PATH=/home/ubuntu/tv-intercom/venv/bin"
ExecStart=/home/ubuntu/tv-intercom/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable tv-intercom
sudo systemctl start tv-intercom
sudo systemctl status tv-intercom
```

### Connessione operatori in esterna

Sostituire `localhost` con l'IP pubblico del VPS:

```
http://IP-VPS:8000/operator/?cam=1   ← Operatore Camera 1
http://IP-VPS:8000/operator/?cam=2   ← Operatore Camera 2
...
```

> **Suggerimento:** creare un QR code per ogni URL e stamparlo sulla back-cover dello smartphone di ogni operatore.

---

## Struttura URL

| URL | Chi la usa |
|---|---|
| `ws://server/ws/camera/{id}` | App PWA operatore (WebSocket) |
| `ws://server/ws/director` | Pannello regia (WebSocket) |
| `POST /api/stt/chunk` | Modulo STT sul laptop regia |
| `POST /api/cues/fire` | Pannello regia (cue manuale) |
| `GET /api/status` | Pannello regia (polling stato) |
| `GET /api/cues` | Pannello regia (lista cue) |
| `POST /api/engine/reset` | Pannello regia (reset per repliche) |
| `GET /operator/?cam=N` | Browser smartphone operatore |
