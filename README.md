# Clerk

An evidence-first shopping research agent. Clerk queries Google Shopping **live**, then returns a buying brief with real product prices, images, retailer links, ratings, delivery details, and cited sources.

## Quick start (double-click)

1. **Double-click `start.bat`** — it starts the backend and opens the app at `http://127.0.0.1:8010`.
2. Type a shopping question and hit **Research this →**.

That's it. No API keys to configure — the demo keys are already wired in for the hackathon.

### Or run it manually

```powershell
cd clerk
python -m uvicorn backend:app --host 127.0.0.1 --port 8010
```

Then open **http://127.0.0.1:8010**.

## Opening index.html directly

You can also double-click `index.html`. The page detects it is running as a
local file and automatically calls the backend at `http://127.0.0.1:8010`.
For that to work the backend must be running (see above).

## Deploy on Vercel (live link for the demo)

1. Import this `clerk` folder into Vercel as the repository root.
2. In **Project Settings → Environment Variables**, add `GROQ_API_KEY` and `SERP_API_KEY` (Production, Preview, and Development).
3. Deploy. `vercel.json` runs `backend.py` (FastAPI) and serves `index.html`.

> Note: `index.html` keeps the keys out of the frontend. When deployed, it
> uses the same-origin `/api/chat` path, so the keys stay on the server.

## What it calls

| Step | Provider | Live? |
|------|----------|-------|
| Product results + prices | SERPAPI Google Shopping | Yes |
| Source citations | SERPAPI Google Search | Yes |
| Buying brief (LLM) | Groq `qwen/qwen3.8-27b` | Yes |
