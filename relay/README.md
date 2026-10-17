# A.F.R.A chat relay

Passes the end-to-end encrypted chat between paired A.F.R.A apps instantly. It only ever stores scrambled data
(apps encrypt with a key made from their pairing code), deletes it after 60 days, and the apps keep their own copy
in the shared library folder, so the chat keeps working (more slowly) if the relay is down.

## Run it on a VPS (Docker)
```bash
cd relay
echo "DOMAIN=chat.example.com" > .env     # a domain pointing at the VPS, or 203-0-113-5.sslip.io for IP 203.0.113.5
docker compose up -d
curl https://chat.example.com/            # {"app":"A.F.R.A chat relay","ok":true}
```
Ports 80 and 443 must be open. Then in A.F.R.A: Settings > Chat > Relay address: `https://chat.example.com`.

## Without Docker
`pip install -r requirements.txt && RELAY_DATA=./data uvicorn relay:app --host 0.0.0.0 --port 8080`, behind any HTTPS proxy.
