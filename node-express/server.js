// Minimal TradingView webhook receiver (Express).
// Verify secret -> ACK fast -> process async -> idempotent on alert_id.
// Run: WEBHOOK_SECRET=change-me node server.js
import express from 'express';
import crypto from 'node:crypto';

const SECRET = process.env.WEBHOOK_SECRET || 'change-me';
const PORT = process.env.PORT || 3000;

// In production use Redis (SET alert_id NX EX 86400) or a DB unique index.
const seen = new Map(); // alert_id -> timestamp
const TTL_MS = 24 * 60 * 60 * 1000;
const queue = [];
let busy = false;

function safeEqual(a, b) {
  const x = Buffer.from(String(a)), y = Buffer.from(String(b));
  return x.length === y.length && crypto.timingSafeEqual(x, y);
}

async function handle(alert) {
  // Slow work here: broker API, risk checks, AI agent...
  console.log('processing', alert.action, alert.ticker, alert.price);
  await new Promise((r) => setTimeout(r, 2000));
}

async function drain() {
  if (busy) return;
  busy = true;
  while (queue.length) {
    const alert = queue.shift();
    try { await handle(alert); } catch (e) { console.error('failed', alert.alert_id, e); }
  }
  busy = false;
}

const app = express();
// TradingView sends text/plain unless the message is valid JSON, so read raw text.
app.use(express.text({ type: '*/*', limit: '64kb' }));

app.post('/webhook', (req, res) => {
  let alert;
  try { alert = JSON.parse(req.body); } catch { return res.status(400).send('bad json'); }
  if (!safeEqual(alert.secret ?? '', SECRET)) return res.status(403).send('forbidden');
  const id = String(alert.alert_id || '');
  if (!id) return res.status(400).send('missing alert_id');

  const now = Date.now();
  for (const [k, t] of seen) if (now - t > TTL_MS) seen.delete(k);
  delete alert.secret;
  if (!seen.has(id)) {
    seen.set(id, now);
    queue.push(alert);
    setImmediate(drain);
  }
  res.status(200).send('ok'); // ACK first, work later
});

app.listen(PORT, () => console.log(`listening on :${PORT}/webhook`));
