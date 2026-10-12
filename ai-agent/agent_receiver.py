"""TradingView alert -> queue -> AI trading agent.

The webhook handler never calls the LLM. LLM calls take seconds and
TradingView gives you ~3 s, so the handler validates, de-duplicates,
enqueues and returns 200. A separate consumer feeds signals to the agent.

The agent below is a stub: plug in your LLM/agent framework of choice.
Keep a deterministic risk gate between the agent and any order API.

Run: WEBHOOK_SECRET=change-me uvicorn agent_receiver:app --port 8001
"""
import asyncio, hmac, json, logging, os, time

from fastapi import FastAPI, Request, Response

SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
MAX_SIGNAL_AGE_S = int(os.environ.get("MAX_SIGNAL_AGE_S", "120"))
log = logging.getLogger("agent")
logging.basicConfig(level=logging.INFO)

app = FastAPI()
signals: asyncio.Queue = asyncio.Queue(maxsize=1000)
seen: dict[str, float] = {}


async def agent_decide(signal: dict) -> dict:
    """Replace with a real agent call (OpenAI, Anthropic, local model, LangGraph...).
    Give it the signal plus context (position, PnL, news) and ask for a structured decision."""
    await asyncio.sleep(3)  # simulate model latency
    return {"decision": "execute" if signal.get("action") in ("buy", "sell") else "skip",
            "reason": "stub agent"}


def risk_gate(signal: dict, decision: dict) -> bool:
    """Deterministic checks the agent can't override."""
    age = time.time() - signal["_received_at"]
    if age > MAX_SIGNAL_AGE_S:
        log.warning("stale signal %s (%.0fs old), skipping", signal["alert_id"], age)
        return False
    return decision.get("decision") == "execute"


async def consumer() -> None:
    while True:
        signal = await signals.get()
        try:
            decision = await agent_decide(signal)
            if risk_gate(signal, decision):
                log.info("EXECUTE %s %s (%s)", signal.get("action"), signal.get("ticker"), decision["reason"])
                # place_order(...)  <- your broker / exchange client
            else:
                log.info("skip %s: %s", signal["alert_id"], decision.get("reason"))
        except Exception:
            log.exception("agent failed on %s", signal.get("alert_id"))
        finally:
            signals.task_done()


@app.on_event("startup")
async def start() -> None:
    asyncio.create_task(consumer())


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    try:
        signal = json.loads(await request.body())
    except ValueError:
        return Response("bad json", status_code=400)
    if not hmac.compare_digest(str(signal.pop("secret", "")), SECRET):
        return Response("forbidden", status_code=403)
    alert_id = str(signal.get("alert_id") or "")
    if not alert_id:
        return Response("missing alert_id", status_code=400)
    now = time.time()
    for k in [k for k, t in seen.items() if now - t > 86400]:
        del seen[k]
    if alert_id in seen:
        return Response("duplicate", status_code=200)
    seen[alert_id] = now
    signal["_received_at"] = now
    try:
        signals.put_nowait(signal)
    except asyncio.QueueFull:
        return Response("busy", status_code=503)  # let the sender retry
    return Response("queued", status_code=200)
