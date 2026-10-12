"""Minimal TradingView webhook receiver (FastAPI).

- verifies a shared secret carried in the alert body (TradingView can't send custom headers)
- ACKs immediately (TradingView cancels requests after ~3 s)
- processes the alert in a background worker
- is idempotent on alert_id (duplicate deliveries are ignored)

Run:  WEBHOOK_SECRET=change-me uvicorn main:app --port 8000
"""
import asyncio, hmac, json, logging, os, sqlite3

from fastapi import FastAPI, Request, Response

SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
DB = sqlite3.connect(os.environ.get("DB_PATH", "seen.db"), check_same_thread=False)
DB.execute("CREATE TABLE IF NOT EXISTS seen (alert_id TEXT PRIMARY KEY)")
log = logging.getLogger("receiver")
logging.basicConfig(level=logging.INFO)

app = FastAPI()
queue: asyncio.Queue = asyncio.Queue()


def first_time(alert_id: str) -> bool:
    """Atomically record alert_id; False if we've already seen it."""
    try:
        with DB:
            DB.execute("INSERT INTO seen (alert_id) VALUES (?)", (alert_id,))
        return True
    except sqlite3.IntegrityError:
        return False


async def handle(alert: dict) -> None:
    # Your slow work goes here: broker API calls, risk checks, an AI agent...
    log.info("processing %s %s %s", alert.get("action"), alert.get("ticker"), alert.get("price"))
    await asyncio.sleep(2)


async def worker() -> None:
    while True:
        alert = await queue.get()
        try:
            await handle(alert)
        except Exception:
            log.exception("failed to process %s", alert.get("alert_id"))
        finally:
            queue.task_done()


@app.on_event("startup")
async def start_worker() -> None:
    asyncio.create_task(worker())


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    raw = await request.body()
    try:
        alert = json.loads(raw)  # TradingView sends text/plain unless the message is valid JSON
    except ValueError:
        return Response("bad json", status_code=400)

    if not hmac.compare_digest(str(alert.get("secret", "")), SECRET):
        return Response("forbidden", status_code=403)

    alert_id = str(alert.get("alert_id") or "")
    if not alert_id:
        return Response("missing alert_id", status_code=400)

    alert.pop("secret", None)
    if first_time(alert_id):
        queue.put_nowait(alert)
    # 200 for duplicates too, so the sender stops retrying
    return Response("ok", status_code=200)
