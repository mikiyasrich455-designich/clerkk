"""
CartSaver (Clerk) backend — FastAPI.

One-store merchant assistant: a shopper chat agent that reads the store's
catalog, answers product questions with real web prices (SERP), and logs
shopper-intent insights for the merchant dashboard.

Endpoints:
    GET  /api/health            -> {ok: true}
    GET  /api/catalog           -> full product catalog
    POST /api/chat              -> {reply, products[], sources[], insight}
    POST /api/search            -> live web price search (SERP + Groq)
    GET  /api/insights          -> aggregated shopper insights for dashboard

LLM: Groq (qwen/qwen3.8-27b). Live prices: SERP Google search.
State is JSON files (hackathon scope — no DB, no cloud).
"""
import concurrent.futures
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(REPO_ROOT, "catalog.json")
INSIGHTS_PATH = os.path.join(REPO_ROOT, "insights.json")

# Vercel serverless detection: read-only filesystem + strict duration limits.
IS_VERCEL = bool(os.environ.get("VERCEL"))

GROQ_KEY = os.environ.get("GROQ_API_KEY") or "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"
SERP_KEY = os.environ.get("SERP_API_KEY") or "3e1ade6589e27599aee2edc05661ece71be4fa47235bc703a63b937e573d0b8b"
GROQ_MODEL = "qwen/qwen3.8-27b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SERP_URL = "https://serpapi.com/search?engine=google"

# Keep total request time well inside Vercel's function duration limit.
SERP_TIMEOUT = 8 if IS_VERCEL else 15
GROQ_TIMEOUT = 9 if IS_VERCEL else 15
SERP_WAIT = 10 if IS_VERCEL else 28

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

app = FastAPI(title="CartSaver Clerk")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# Embedded copy of catalog.json so the agent keeps working even when the
# JSON files are not present next to the function (Vercel serverless).
EMBEDDED_CATALOG = {"store": "Mika's Threads", "products": [
    {"name": "Aria Linen Midi Dress", "category": "Dresses", "price": "$89", "sizes": ["XS", "S", "M", "L"], "colors": ["Sage", "Blush"], "in_stock": True, "tags": ["dress", "linen", "summer", "midi"]},
    {"name": "Nova Silk Slip Dress", "category": "Dresses", "price": "$119", "sizes": ["XS", "S", "M"], "colors": ["Champagne", "Black"], "in_stock": True, "tags": ["dress", "silk", "slip", "evening"]},
    {"name": "Halo Wrap Dress", "category": "Dresses", "price": "$99", "sizes": ["S", "M", "L", "XL"], "colors": ["Terracotta", "Navy"], "in_stock": True, "tags": ["dress", "wrap", "flattering"]},
    {"name": "Echo Oversized Tee", "category": "Tops", "price": "$34", "sizes": ["S", "M", "L", "XL"], "colors": ["White", "Black", "Oat"], "in_stock": True, "tags": ["tee", "t-shirt", "casual", "oversized"]},
    {"name": "Juno Ribbed Knit Top", "category": "Tops", "price": "$42", "sizes": ["XS", "S", "M", "L"], "colors": ["Cream", "Olive"], "in_stock": True, "tags": ["top", "knit", "ribbed", "fitted"]},
    {"name": "Sable Silk Blouse", "category": "Tops", "price": "$78", "sizes": ["S", "M", "L"], "colors": ["Ivory", "Emerald"], "in_stock": True, "tags": ["blouse", "silk", "work", "office"]},
    {"name": "Atlas Wide-Leg Trouser", "category": "Bottoms", "price": "$95", "sizes": ["24", "26", "28", "30"], "colors": ["Charcoal", "Sand"], "in_stock": True, "tags": ["trouser", "wide-leg", "tailored", "work"]},
    {"name": "Mira High-Rise Denim", "category": "Bottoms", "price": "$110", "sizes": ["24", "25", "26", "27", "28", "29"], "colors": ["Indigo", "Washed Black"], "in_stock": True, "tags": ["jeans", "denim", "high-rise", "straight"]},
    {"name": "Vela Pleated Skirt", "category": "Bottoms", "price": "$68", "sizes": ["XS", "S", "M", "L"], "colors": ["Navy", "Burgundy"], "in_stock": True, "tags": ["skirt", "pleated", "midi"]},
    {"name": "Onyx Leather Jacket", "category": "Outerwear", "price": "$240", "sizes": ["S", "M", "L"], "colors": ["Black", "Mocha"], "in_stock": False, "tags": ["jacket", "leather", "moto", "outerwear"]},
    {"name": "Aurora Trench Coat", "category": "Outerwear", "price": "$180", "sizes": ["XS", "S", "M", "L"], "colors": ["Camel", "Stone"], "in_stock": True, "tags": ["coat", "trench", "classic"]},
    {"name": "Cove Puffer Jacket", "category": "Outerwear", "price": "$150", "sizes": ["S", "M", "L", "XL"], "colors": ["Olive", "Black"], "in_stock": True, "tags": ["jacket", "puffer", "winter", "warm"]},
    {"name": "Lune Cashmere Scarf", "category": "Accessories", "price": "$55", "sizes": ["One Size"], "colors": ["Grey", "Camel"], "in_stock": True, "tags": ["scarf", "cashmere", "accessory", "winter"]},
    {"name": "Orbit Tote Bag", "category": "Accessories", "price": "$120", "sizes": ["One Size"], "colors": ["Tan", "Black"], "in_stock": True, "tags": ["bag", "tote", "leather", "everyday"]},
    {"name": "Dune Heeled Sandal", "category": "Shoes", "price": "$85", "sizes": ["6", "7", "8", "9"], "colors": ["Nude", "Black"], "in_stock": True, "tags": ["shoes", "sandal", "heel", "summer"]},
    {"name": "Peak White Sneaker", "category": "Shoes", "price": "$95", "sizes": ["7", "8", "9", "10", "11"], "colors": ["White", "White/Gum"], "in_stock": True, "tags": ["shoes", "sneaker", "white", "casual"]},
    {"name": "Ridge Chelsea Boot", "category": "Shoes", "price": "$135", "sizes": ["7", "8", "9", "10"], "colors": ["Brown", "Black"], "in_stock": True, "tags": ["boots", "chelsea", "leather"]},
    {"name": "Petal Floral Wrap Skirt", "category": "Bottoms", "price": "$62", "sizes": ["XS", "S", "M"], "colors": ["Floral"], "in_stock": True, "tags": ["skirt", "floral", "spring", "wrap"]},
    {"name": "Ember Cropped Cardigan", "category": "Tops", "price": "$48", "sizes": ["XS", "S", "M", "L"], "colors": ["Oat", "Rose"], "in_stock": True, "tags": ["cardigan", "knit", "cropped", "layer"]},
    {"name": "Lumen Satin Midi Skirt", "category": "Bottoms", "price": "$72", "sizes": ["S", "M", "L"], "colors": ["Champagne", "Ink"], "in_stock": True, "tags": ["skirt", "satin", "midi", "evening"]},
    {"name": "Iris Peplum Top", "category": "Tops", "price": "$54", "sizes": ["XS", "S", "M", "L"], "colors": ["Lilac", "White"], "in_stock": True, "tags": ["top", "peplum", "blouse"]},
    {"name": "Zephyr Linen Shirt", "category": "Tops", "price": "$66", "sizes": ["S", "M", "L", "XL"], "colors": ["White", "Sky"], "in_stock": True, "tags": ["shirt", "linen", "button-down", "summer"]},
    {"name": "Noor Embroidered Kaftan", "category": "Dresses", "price": "$140", "sizes": ["S", "M", "L"], "colors": ["Sand", "Ocean"], "in_stock": True, "tags": ["kaftan", "embroidered", "resort", "dress"]},
    {"name": "Rue Straight Trousers", "category": "Bottoms", "price": "$88", "sizes": ["24", "26", "28", "30", "32"], "colors": ["Black", "Stone"], "in_stock": True, "tags": ["trouser", "straight", "work", "office"]},
    {"name": "Sol Straw Fedora", "category": "Accessories", "price": "$38", "sizes": ["One Size"], "colors": ["Natural"], "in_stock": True, "tags": ["hat", "straw", "fedora", "summer"]},
    {"name": "Gemma Hoop Earrings", "category": "Accessories", "price": "$28", "sizes": ["One Size"], "colors": ["Gold", "Silver"], "in_stock": True, "tags": ["earrings", "hoop", "jewelry", "gold"]},
    {"name": "Cinder Suede Ankle Boot", "category": "Shoes", "price": "$145", "sizes": ["6", "7", "8", "9"], "colors": ["Taupe", "Black"], "in_stock": True, "tags": ["boot", "suede", "ankle"]},
    {"name": "Harbor Belted Blazer", "category": "Outerwear", "price": "$160", "sizes": ["XS", "S", "M", "L"], "colors": ["Black", "Pinstripe"], "in_stock": True, "tags": ["blazer", "belted", "work", "tailored"]},
    {"name": "Opal Silk Bandana", "category": "Accessories", "price": "$22", "sizes": ["One Size"], "colors": ["Multicolor"], "in_stock": True, "tags": ["bandana", "silk", "scarf", "accessory"]},
    {"name": "Fern Slip-On Loafer", "category": "Shoes", "price": "$98", "sizes": ["7", "8", "9", "10"], "colors": ["Forest", "Black"], "in_stock": True, "tags": ["shoes", "loafer", "slip-on", "work"]},
]}

# In-memory fallback when the filesystem is read-only (Vercel serverless).
_MEM_INSIGHT_LOG = []


def _catalog():
    data = _read_json(CATALOG_PATH, None)
    if isinstance(data, dict) and data.get("products"):
        return data
    return EMBEDDED_CATALOG


def _insights():
    data = _read_json(INSIGHTS_PATH, None)
    if not isinstance(data, dict) or "log" not in data:
        data = {"log": []}
    if _MEM_INSIGHT_LOG:
        data = {"log": (_MEM_INSIGHT_LOG + data.get("log", []))[:200]}
    return data


def _log_insight(entry):
    entry["ts"] = int(time.time())
    try:
        data = _read_json(INSIGHTS_PATH, {"log": []})
        entry["id"] = str(len(data["log"]) + 1)
        data["log"].insert(0, entry)
        data["log"] = data["log"][:200]
        _write_json(INSIGHTS_PATH, data)
    except Exception:
        # Read-only filesystem (Vercel) — keep insights in memory only.
        entry["id"] = str(len(_MEM_INSIGHT_LOG) + 1)
        _MEM_INSIGHT_LOG.insert(0, entry)
        del _MEM_INSIGHT_LOG[200:]


# ---------------------------------------------------------------------------
# LLM + web search
# ---------------------------------------------------------------------------
def _groq(messages, max_tokens=600, temperature=0.4, json_mode=False):
    """Call Groq. Returns text or None on failure."""
    if not GROQ_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")
    body = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + GROQ_KEY,
            "User-Agent": UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"].strip()


def _serp(query, num=8):
    """Live Google search via SERP. Returns list of {title, link, snippet, price, image, source}."""
    if not SERP_KEY:
        raise RuntimeError("SERP_API_KEY is not configured")
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERP_KEY,
        "num": str(num),
        "gl": "us",
        "hl": "en",
    }
    url = SERP_URL + "&" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if k != "engine"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=SERP_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))

    out = []
    # Shopping results have prices + thumbnails — best for commerce
    for s in data.get("shopping_results", [])[:num]:
        price = s.get("price") or (
            f"${s['extracted_price']}" if s.get("extracted_price") else ""
        )
        out.append({
            "title": s.get("title", ""),
            "link": s.get("link") or s.get("product_link", "") or "",
            "snippet": s.get("source") or s.get("snippet") or "",
            "price": price,
            "image": s.get("thumbnail") or "",
            "source": s.get("source") or "",
            "rating": s.get("rating") or "",
            "reviews": s.get("reviews") or "",
        })
    for res in data.get("organic_results", [])[:num]:
        out.append({
            "title": res.get("title", ""),
            "link": res.get("link", ""),
            "snippet": (res.get("snippet") or "")[:220],
            "price": res.get("price") or "",
            "image": (res.get("thumbnail") or ""),
            "source": (res.get("displayed_link") or "").split("/")[0],
        })
    return out


def _serp_shopping(query, num=8):
    """Google Shopping engine — richer product data (image, price, source, link)."""
    if not SERP_KEY:
        raise RuntimeError("SERP_API_KEY is not configured")
    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": SERP_KEY,
        "num": str(num),
        "gl": "us",
        "hl": "en",
    }
    url = "https://serpapi.com/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=SERP_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = []
    for s in data.get("shopping_results", [])[:num]:
        price = s.get("price")
        if not price and s.get("extracted_price"):
            price = f"${s['extracted_price']}"
        out.append({
            "title": s.get("title", ""),
            "link": s.get("product_link") or s.get("link", "") or "",
            "snippet": s.get("source") or "",
            "price": price or "",
            "image": s.get("thumbnail") or "",
            "source": s.get("source") or "",
            "rating": s.get("rating") or "",
            "reviews": s.get("reviews") or "",
            "delivery": s.get("delivery") or "",
        })
    return out


# ---------------------------------------------------------------------------
# Matching helpers (deterministic rules first — reliable demo)
# ---------------------------------------------------------------------------
def _match_products(query):
    """Deterministic catalog search over name, category, tags."""
    q = query.lower()
    prods = _catalog().get("products", [])
    scored = []
    for p in prods:
        hay = " ".join([
            p.get("name", ""),
            p.get("category", ""),
            " ".join(p.get("tags", [])),
        ]).lower()
        score = 0
        if q in hay:
            score += 5
        for word in q.split():
            if word and word in hay:
                score += 2
        # size/color matches
        for attr in p.get("sizes", []) + p.get("colors", []):
            if attr.lower() in q:
                score += 2
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:5]]


def _price_talk(query, products):
    """Compose the shopping-assistant reply from catalog + live web prices."""
    # Try live prices for the top product
    sources = []
    price_note = ""
    if products:
        top = products[0]
        try:
            hits = _serp(top.get("name") + " price", num=5)
            prices = [h for h in hits if h.get("price")]
            if prices:
                price_note = (
                    f"Live web prices for the {top['name']}: "
                    + " · ".join(f"{h['title'][:40]} {h['price']}" for h in prices[:3])
                )
                sources = hits[:3]
        except Exception:
            pass

    lines = []
    for p in products[:3]:
        price = p.get("price", "Contact for price")
        sizes = ", ".join(p.get("sizes", [])) or "one size"
        stock = "in stock" if p.get("in_stock", True) else "out of stock"
        lines.append(
            f"• {p['name']} — {price} ({sizes}; {stock})"
        )
    if not lines:
        lines.append("I couldn't find that in our catalog — try another search term.")

    reply = "\n".join(lines)
    if price_note:
        reply += "\n\n" + price_note
    return reply, sources


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "model": GROQ_MODEL}


@app.get("/api/catalog")
def catalog():
    return _catalog()


@app.get("/api/insights")
def insights():
    data = _insights()
    log = data.get("log", [])
    total = len(log)
    categories = {}
    for e in log:
        c = e.get("category", "general")
        categories[c] = categories.get(c, 0) + 1
    top_queries = {}
    for e in log:
        q = e.get("query", "").strip()
        if q:
            top_queries[q] = top_queries.get(q, 0) + 1
    return {
        "total_sessions": total,
        "categories": categories,
        "top_queries": sorted(
            top_queries.items(), key=lambda x: -x[1]
        )[:8],
        "recent": log[:15],
        "recovered_hint": "Live once checkout is connected",
    }


@app.post("/api/chat")
def chat(payload: dict = Body(...)):
    """Live shopping agent: real Google Shopping results + Groq buying brief."""
    query = (payload.get("message") or "").strip()
    if not query:
        return JSONResponse({"error": "message is required"}, status_code=400)

    # 1) ALWAYS run live web research first — the two SERP calls run in parallel
    live_products = []
    live_sources = []
    def _get_shopping():
        return _serp_shopping(query, num=8)
    def _get_search():
        return _serp(query, num=8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_get_shopping)
        f2 = ex.submit(_get_search)
        try:
            live_products = f1.result(timeout=SERP_WAIT)
        except Exception as e:
            live_products = []
            print("SERP shopping failed:", e)
        try:
            live_sources = f2.result(timeout=SERP_WAIT)
        except Exception as e:
            live_sources = []
            print("SERP search failed:", e)

    # 2) Also try the local catalog (kept as a helpful sidecar)
    catalog_matches = _match_products(query)

    # Build the primary product cards.
    # Prefer real Google Shopping results (they have image + price + link).
    products = []
    for item in live_products[:6]:
        products.append({
            "name": item.get("title") or "Product",
            "category": item.get("source") or "Web result",
            "price": item.get("price") or "",
            "sizes": [],
            "in_stock": True,
            "image": item.get("image") or "",
            "link": item.get("link") or "",
            "source": item.get("source") or "",
            "rating": item.get("rating") or "",
            "reviews": item.get("reviews") or "",
            "delivery": item.get("delivery") or "",
        })

    # If Google returned nothing usable, fall back to the local catalog.
    if not products and catalog_matches:
        for p in catalog_matches[:4]:
            products.append({
                "name": p.get("name"),
                "category": p.get("category", "Product"),
                "price": p.get("price", ""),
                "sizes": p.get("sizes", []),
                "in_stock": p.get("in_stock", True),
                "image": "",
                "link": "https://www.google.com/search?q=" + urllib.parse.quote(p.get("name", "")),
                "source": "Mika's Threads",
            })

    # Sources shown in the right column
    sources = []
    for s in (live_sources or [])[:6]:
        if not s.get("title"):
            continue
        sources.append({
            "title": s.get("title"),
            "link": s.get("link", ""),
            "snippet": s.get("snippet", ""),
            "price": s.get("price", ""),
        })

    # Build a short, honest buying brief with Groq — grounded in the real data.
    context_lines = []
    for p in products[:5]:
        line = f"{p['name']}"
        if p.get("price"):
            line += f" — {p['price']}"
        if p.get("source"):
            line += f" ({p['source']})"
        if p.get("rating"):
            line += f", rating {p['rating']}"
        context_lines.append(line)
    context = "\n".join(context_lines) if context_lines else "(no live results)"

    reply = None
    try:
        reply = _groq([
            {"role": "system",
             "content": "You are Clerk, an evidence-first shopping research agent. "
                        "You will be given the shopper's request and REAL live results from "
                        "Google Shopping (title, price, source). Write a concise 3-5 sentence "
                        "buying brief that names the best options and their real prices. "
                        "Never invent prices — only cite the ones provided. If nothing fits "
                        "the budget, say so honestly."},
            {"role": "user",
             "content": f"Shopper request: {query}\n\n"
                        f"Live Google Shopping results:\n{context}\n\n"
                        "Write the buying brief now."},
        ], max_tokens=320)
    except Exception as e:
        reply = f"I couldn't complete the research. Error: {str(e)}"
        print("Groq failed:", e)

    if not reply or len(reply) < 5:
        # Deterministic fallback
        if products:
            top = products[:3]
            reply = (
                f"Here are the strongest live matches for '{query}':\n"
                + "\n".join(f"• {p['name']} — {p.get('price','—')} ({p.get('source','')})" for p in top)
            )
        else:
            reply = f"I couldn't find live results for '{query}' right now. Try more specific product terms."

    # Log insight for the merchant dashboard
    cat = (products[0].get("category", "general") if products else "general")
    _log_insight({
        "query": query,
        "category": cat,
        "matched": len(products),
        "products": [p.get("name") for p in products[:3]],
        "live": bool(live_products),
    })

    return {
        "reply": reply,
        "products": products,
        "sources": sources,
        "live": bool(live_products),
    }


@app.post("/api/search")
def search(payload: dict = Body(...)):
    """Raw live web price search."""
    query = (payload.get("q") or payload.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "q is required"}, status_code=400)
    try:
        hits = _serp(query, num=8)
        return {"query": query, "results": hits}
    except Exception as e:
        return JSONResponse({"error": f"search failed: {e}"}, status_code=500)


# Serve the single-file frontend (localhost). On Vercel the HTML is served
# statically by the platform, so this mount is only a fallback.
try:
    if os.path.exists(os.path.join(REPO_ROOT, "index.html")):
        app.mount("/", StaticFiles(directory=REPO_ROOT, html=True), name="frontend")
except Exception:
    pass
