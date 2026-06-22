# EnoxAI

FastAPI + LangGraph single-agent ecommerce customer support assistant.

## Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill in your API keys
uvicorn main:app --reload --host 0.0.0.0 --port 9000
```

On first startup: loads faq.json → tries loading saved FAISS index → builds fresh if none exists (~5-10s, calls OpenAI embeddings).

## Environment variables

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small

FAQ_DATA_PATH=data/faq.json
FAISS_INDEX_PATH=data/faiss_index
CHAT_STORE_PATH=data/enoxai.db
TOP_K_RESULTS=4
BM25_WEIGHT=0.4
SEMANTIC_WEIGHT=0.6

ENOX_API_URL=http://localhost:8000
ENOX_API_KEY=your-internal-key-here

LANGSMITH_TRACING=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=
LANGSMITH_PROJECT="EnoXAI"
```

## Architecture

One LangGraph ReAct agent. 13 tools. One reasoning loop.

```
Customer message
       ↓
LangGraph ReAct agent  (single LLM call per turn)
       ├── search_knowledge_base   ← FAQ retrieval (FAISS + BM25)
       ├── get_order_status
       ├── cancel_order
       ├── get_order_details
       ├── check_order_incident
       ├── send_order_invoice
       ├── get_shipping_address
       ├── update_shipping_address
       ├── create_return_request
       ├── check_order_return_request_status
       ├── what_does_enorsia_sale
       ├── validate_discount_code
       └── create_support_ticket
       ↓
Final answer
```

The agent decides which tool(s) to call. No router. No classifier. No separate paths.

```
FAQ CATEGORIES

about_creator
accounts_and_login
checkout_process
company_information
complaints
contact_and_support
custom_orders
delivery_failure
delivery_locations
discounts_and_offers
exchange_and_replacement
mobile_app
order_cancellations
order_tracking
order_verification
orders
packaging_and_gifting
payments
returns_and_refunds
reviews_and_feedback
security_and_privacy
shipping_and_delivery
stock_and_availability
store_policies
subscriptions_and_loyalty
taxes_and_duties
wholesale_and_b2b
```

## Project structure

```
backend/
├── data/
│   ├── faq.json               # FAQ knowledge base
|   ├── products.json          # Product knowledge base
│   └── faiss_index/           # auto-created on first run
|   |__ product_faiss_index    #auto-created on first run
|   └── enoxai.db              # Chat History database auto-created on first run 
│
├── app/
│   ├── __init__.py
│   ├── config.py              # Settings via pydantic-settings + .env
│   ├── models.py              # All Pydantic models (tools + RAG + API)
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   └── graph.py           # LangGraph ReAct agent — all 13 tools wired up
|   |   └── prompt.py          # LLM System prompt
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   └── engine.py          # FAISS + BM25 hybrid retrieval engine for FAQ
|   |   └── product_engine.py  # FAISS + BM25 hybrid retrieval engine for product
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   └── tools.py          # All 13 @tool functions + ALL_TOOLS list
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   └── utils.py           # post_to_api, error_response, sanitize_optional_str
│   │
│   └── api/
│   │   ├── __init__.py
│   │   └── routes.py          # FastAPI endpoints
│   │── databases/
│   │   ├── __init__.py
│   │   ├── config.py          # Sqlite db config
│   │   ├── chat_store.py      # Store data to db
├── main.py
├── requirements.txt
├── README.md
├── .env.example
└── note.txt
```

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/chat` | Chat — full JSON response |
| `POST` | `/api/v1/chat/stream` | Chat — Server-Sent Events stream |
| `POST` | `/api/v1/index/build` | Rebuild FAISS index after editing faq.json |
| `GET`  | `/api/v1/index/status` | Index health and document count |
| `GET`  | `/api/v1/health` | Liveness probe |
| `POST` | `/api/v1/debug/retrieve` | Raw retrieval results without LLM |
| `GET`  | `/api/v1//chat/history` | Retrive current session chat history|

Interactive docs: `http://localhost:8000/docs`

## Usage examples

### General question (agent calls search_knowledge_base)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is your return policy?", "session_id": "user_123"}'
```

```json
{
  "session_id": "user_123",
  "answer": "Our return policy allows returns within 30 days...",
  "tool_calls": ["search_knowledge_base"]
}
```

### Order question (agent calls backend tool)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Where is my order EN-10492?", "session_id": "user_123"}'
```

```json
{
  "session_id": "user_123",
  "answer": "Your order EN-10492 has been shipped. Tracking: BD-TRK-88821...",
  "tool_calls": ["get_order_status"]
}
```

### Mixed question (agent calls both)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to return my order EN-10492, what is the policy?", "session_id": "user_123"}'
```

```json
{
  "session_id": "user_123",
  "answer": "Our return policy allows 30 days... For your order EN-10492...",
  "tool_calls": ["search_knowledge_base", "get_order_details"]
}
```

### Streaming

```bash
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Cancel my order EN-10492", "session_id": "user_123"}'
```

```
data: {"token": "I"}
data: {"token": " can"}
data: {"token": " help"}
...
data: {"done": true, "session_id": "user_123", "tool_calls": ["cancel_order"]}
```

JavaScript client:

```js
const res = await fetch("/api/v1/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "Cancel my order", session_id: "abc" }),
});
const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split("\n\n");
  buffer = lines.pop();
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const data = JSON.parse(line.slice(6));
    if (data.token) appendToChat(data.token);
    if (data.done)  markComplete(data);
    if (data.error) showError(data.error);
  }
}
```

## The 13 tools

| Tool | Domain | Description |
|---|---|---|
| `search_knowledge_base` | Knowledge | FAISS+BM25 search over faq.json |
| `get_order_status` | Orders | Live tracking and fulfilment status |
| `cancel_order` | Orders | Cancel an order |
| `get_order_details` | Orders | Full order breakdown |
| `check_order_incident` | Orders | Check for open incidents |
| `send_order_invoice` | Orders | Email the invoice |
| `get_shipping_address` | Shipping | View delivery address on file |
| `update_shipping_address` | Shipping | Update delivery address fields |
| `create_return_request` | Returns | Submit a return |
| `get_check_order_return_request_status` | Returns | Track a submitted return |
| `what_does_enorsia_sale` | Knowledge | What kind of product sale |
| `validate_discount_code` | Store ops | Validate a coupon or promo code |
| `create_support_ticket` | Store ops | Open a manual support ticket |

## Adding new FAQ entries

1. Add an entry to `data/faq.json`:

```json
{
  "id": "ship_001",
  "category": "shipping",
  "action_type": "static_faq",
  "question": "How long does delivery take?",
  "answer": "Standard delivery takes 3-5 business days.",
  "keywords": ["delivery time", "how long", "shipping duration"],
  "embedding_text": "How long does delivery take? Standard delivery 3-5 business days. Keywords: delivery time, how long, shipping duration.",
  "metadata": {
    "category": "shipping",
    "action_type": "static_faq",
    "source": "faq_v1",
    "updated_at": "2025-01-01"
  }
}
```

2. Rebuild the index:

```bash
curl -X POST http://localhost:8000/api/v1/index/build
```

## Tuning retrieval

| Variable | Default | Effect |
|---|---|---|
| `SEMANTIC_WEIGHT` | `0.6` | FAISS conceptual matching weight |
| `BM25_WEIGHT` | `0.4` | Keyword exact-match weight |
| `TOP_K_RESULTS` | `4` | FAQ docs retrieved per query |

Weights must sum to 1.0. Changing weights only requires a server restart — not an index rebuild.