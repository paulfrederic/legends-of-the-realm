/**
 * Stripe -> GA4 purchase bridge.
 *
 * Why this exists: Stripe Payment Links complete checkout on Stripe's own
 * domain, so the browser never sees the conversion and a client-side
 * `purchase` event can never fire. Without this function, begin_checkout is
 * the last thing GA4 ever hears about and revenue and ROAS are invisible.
 *
 * Attribution: the page packs the GA4 client_id and session_id into the
 * payment link's client_reference_id before redirecting, and this function
 * unpacks them, so the purchase lands on the same user and session that
 * started the visit rather than appearing as a brand new direct session.
 *
 * Signature verification is implemented directly against node:crypto so this
 * stays a zero-dependency function on an otherwise static site. No build step,
 * no node_modules, nothing to keep patched.
 *
 * Required environment variables (set these in the Vercel dashboard, never in
 * the repo):
 *   STRIPE_WEBHOOK_SECRET  whsec_...   from the Stripe webhook endpoint
 *   GA4_API_SECRET         from GA4 Admin > Data streams > Measurement Protocol
 *   GA4_MEASUREMENT_ID     G-31WLZW9T5Q
 *   GA4_DEBUG              optional, "1" routes to GA4's validation endpoint
 */
const crypto = require('crypto');

const TOLERANCE_SECONDS = 300;   // reject replays older than five minutes

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ error: 'method not allowed' });
  }

  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  const mid    = process.env.GA4_MEASUREMENT_ID;
  const apiSec = process.env.GA4_API_SECRET;
  if (!secret || !mid || !apiSec) {
    console.error('[legends] missing env', {
      STRIPE_WEBHOOK_SECRET: !!secret, GA4_MEASUREMENT_ID: !!mid, GA4_API_SECRET: !!apiSec });
    // 500 so Stripe retries once the config is fixed, rather than silently dropping revenue
    return res.status(500).json({ error: 'not configured' });
  }

  let raw;
  try {
    raw = await readRawBody(req);
  } catch (e) {
    return res.status(400).json({ error: 'unreadable body' });
  }

  const verdict = verifyStripeSignature(raw, req.headers['stripe-signature'], secret);
  if (!verdict.ok) {
    console.warn('[legends] signature rejected:', verdict.reason);
    return res.status(400).json({ error: 'invalid signature' });
  }

  let event;
  try {
    event = JSON.parse(raw.toString('utf8'));
  } catch (e) {
    return res.status(400).json({ error: 'invalid json' });
  }

  // Acknowledge everything. Anything we do not handle is a no-op, not an error,
  // otherwise Stripe retries events we deliberately ignore.
  if (event.type !== 'checkout.session.completed') {
    return res.status(200).json({ received: true, ignored: event.type });
  }

  const s = event.data && event.data.object ? event.data.object : {};
  if (s.payment_status && s.payment_status !== 'paid') {
    return res.status(200).json({ received: true, ignored: 'unpaid session' });
  }

  const ids      = unpackRef(s.client_reference_id);
  const currency = (s.currency || 'eur').toUpperCase();
  const value    = typeof s.amount_total === 'number' ? s.amount_total / 100 : 0;
  const qty      = quantityFrom(s);
  const locale   = localeFrom(s);

  // transaction_id is the Stripe session id, so GA4 dedupes Stripe's retries for us.
  const payload = {
    client_id: ids.clientId || syntheticClientId(s.id),
    non_personalized_ads: true,
    events: [{
      name: 'purchase',
      params: {
        transaction_id: s.id,
        value: value,
        currency: currency,
        shipping: 0,
        engagement_time_msec: 1,
        ...(ids.sessionId ? { session_id: ids.sessionId } : {}),
        ...(locale ? { edition: locale } : {}),
        stitched: ids.clientId ? 'yes' : 'no',
        items: [{
          item_id: 'LOTR-FE-78',
          item_name: 'Legends of the Realm, First Edition',
          item_brand: 'PsychicWorld',
          item_category: 'Tarot deck',
          ...(locale ? { item_variant: locale } : {}),
          price: qty > 0 ? Number((value / qty).toFixed(2)) : value,
          quantity: qty || 1
        }]
      }
    }]
  };

  const base = process.env.GA4_DEBUG === '1'
    ? 'https://www.google-analytics.com/debug/mp/collect'
    : 'https://www.google-analytics.com/mp/collect';
  const url = base + '?measurement_id=' + encodeURIComponent(mid)
                   + '&api_secret=' + encodeURIComponent(apiSec);

  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = process.env.GA4_DEBUG === '1' ? await r.text() : '';
    console.log('[legends] purchase sent', {
      session: s.id, value, currency, stitched: !!ids.clientId, ga4Status: r.status, body });
  } catch (e) {
    // Never fail the webhook on a GA4 hiccup. Stripe would retry the whole
    // event and the payment itself is not in doubt.
    console.error('[legends] GA4 send failed', e && e.message);
  }

  return res.status(200).json({ received: true });
};

function readRawBody(req) {
  if (Buffer.isBuffer(req.body)) return Promise.resolve(req.body);
  if (typeof req.body === 'string') return Promise.resolve(Buffer.from(req.body, 'utf8'));
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function verifyStripeSignature(raw, header, secret) {
  if (!header) return { ok: false, reason: 'no signature header' };
  let timestamp = null;
  const provided = [];
  for (const part of String(header).split(',')) {
    const idx = part.indexOf('=');
    if (idx === -1) continue;
    const k = part.slice(0, idx).trim();
    const v = part.slice(idx + 1).trim();
    if (k === 't') timestamp = v;
    else if (k === 'v1') provided.push(v);
  }
  if (!timestamp || !provided.length) return { ok: false, reason: 'malformed header' };

  const age = Math.floor(Date.now() / 1000) - parseInt(timestamp, 10);
  if (!Number.isFinite(age) || Math.abs(age) > TOLERANCE_SECONDS) {
    return { ok: false, reason: 'timestamp outside tolerance (' + age + 's)' };
  }

  const expected = crypto.createHmac('sha256', secret)
    .update(Buffer.concat([Buffer.from(timestamp + '.', 'utf8'), raw]))
    .digest('hex');
  const exp = Buffer.from(expected, 'utf8');
  const match = provided.some((sig) => {
    const got = Buffer.from(sig, 'utf8');
    return got.length === exp.length && crypto.timingSafeEqual(got, exp);
  });
  return match ? { ok: true } : { ok: false, reason: 'no matching v1 signature' };
}

/** "1234567890_1234567890-1755500000" -> {clientId, sessionId} */
function unpackRef(ref) {
  if (!ref || typeof ref !== 'string') return {};
  const dash = ref.lastIndexOf('-');
  const cidPart = dash === -1 ? ref : ref.slice(0, dash);
  const sid     = dash === -1 ? '' : ref.slice(dash + 1);
  const clientId = cidPart.replace('_', '.');
  if (!/^\d+\.\d+$/.test(clientId)) return {};
  return { clientId, sessionId: /^\d+$/.test(sid) ? sid : undefined };
}

/**
 * When the buyer declined analytics consent there is no _ga cookie and so no
 * client_id. Revenue still has to be counted, so derive a stable id from the
 * Stripe session: deterministic, so retries dedupe, and it cannot collide with
 * a real GA client_id because the second component is fixed.
 */
function syntheticClientId(sessionId) {
  const h = crypto.createHash('sha256').update(String(sessionId)).digest();
  return (h.readUInt32BE(0) % 4294967295) + '.0';
}

function quantityFrom(s) {
  const q = s.metadata && s.metadata.quantity ? parseInt(s.metadata.quantity, 10) : NaN;
  return Number.isFinite(q) && q > 0 ? q : 1;
}

function localeFrom(s) {
  const m = s.metadata || {};
  const v = m.edition || m.language || m.lang;
  return typeof v === 'string' && /^(en|nl|fr)$/.test(v) ? v : '';
}

module.exports.config = { api: { bodyParser: false } };

// Exposed for the test harness only. Not part of the request path.
module.exports._internals = {
  verifyStripeSignature, unpackRef, syntheticClientId, quantityFrom, localeFrom
};
