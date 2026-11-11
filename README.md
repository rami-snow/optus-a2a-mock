# Optus Contracts & CRM Agent (mock A2A server)

Render settings
- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Environment variables: `API_KEY` (any secret string), `PUBLIC_URL` (the service's https URL, no trailing slash)

Check after deploy
- Agent card (public): `curl -s $PUBLIC_URL/.well-known/agent-card.json`
- Confirm the `url` field shows the https Render URL, not localhost.
