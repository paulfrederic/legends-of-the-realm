const crypto = require('crypto');
const mod = require(require('path').join(__dirname,'../api/stripe-webhook.js'));
const { verifyStripeSignature, unpackRef, syntheticClientId, quantityFrom, localeFrom } = mod._internals;

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; }
  else { fail++; console.log('  FAIL:', name, extra === undefined ? '' : JSON.stringify(extra)); }
}

const SECRET = 'whsec_testsecret_abc123';
const body = Buffer.from(JSON.stringify({ id: 'evt_1', type: 'checkout.session.completed' }), 'utf8');
function sign(raw, secret, ts) {
  return crypto.createHmac('sha256', secret)
    .update(Buffer.concat([Buffer.from(ts + '.', 'utf8'), raw])).digest('hex');
}
const now = Math.floor(Date.now() / 1000);

// --- signature verification ---
const good = sign(body, SECRET, now);
ok('valid signature accepted', verifyStripeSignature(body, `t=${now},v1=${good}`, SECRET).ok);

ok('tampered body rejected',
   !verifyStripeSignature(Buffer.from(body.toString() + ' '), `t=${now},v1=${good}`, SECRET).ok);

ok('wrong secret rejected',
   !verifyStripeSignature(body, `t=${now},v1=${good}`, 'whsec_wrong').ok);

const oldTs = now - 3600;
ok('replay outside tolerance rejected',
   !verifyStripeSignature(body, `t=${oldTs},v1=${sign(body, SECRET, oldTs)}`, SECRET).ok);

const future = now + 3600;
ok('far-future timestamp rejected',
   !verifyStripeSignature(body, `t=${future},v1=${sign(body, SECRET, future)}`, SECRET).ok);

ok('missing header rejected', !verifyStripeSignature(body, undefined, SECRET).ok);
ok('malformed header rejected', !verifyStripeSignature(body, 'garbage', SECRET).ok);
ok('header with no v1 rejected', !verifyStripeSignature(body, `t=${now}`, SECRET).ok);

// Stripe sends multiple v1 sigs during secret rotation; any one matching is valid
ok('multiple v1, one valid, accepted',
   verifyStripeSignature(body, `t=${now},v1=${'0'.repeat(64)},v1=${good}`, SECRET).ok);
ok('multiple v1, none valid, rejected',
   !verifyStripeSignature(body, `t=${now},v1=${'0'.repeat(64)},v1=${'1'.repeat(64)}`, SECRET).ok);

// a signature of the wrong LENGTH must not throw (timingSafeEqual throws on length mismatch)
let threw = false;
try { verifyStripeSignature(body, `t=${now},v1=abc`, SECRET); } catch (e) { threw = true; }
ok('short signature does not throw', !threw);

// signature over the timestamp must actually bind it: reusing a sig with a different t fails
const t2 = now - 10;
ok('signature bound to timestamp', !verifyStripeSignature(body, `t=${t2},v1=${good}`, SECRET).ok);

// --- client_reference_id round trip ---
ok('unpack full ref', JSON.stringify(unpackRef('1234567890_1234567890-1755500000'))
   === JSON.stringify({ clientId: '1234567890.1234567890', sessionId: '1755500000' }));
ok('unpack client only', JSON.stringify(unpackRef('1234567890_1234567890'))
   === JSON.stringify({ clientId: '1234567890.1234567890', sessionId: undefined }));
ok('reject junk ref', JSON.stringify(unpackRef('hello-world')) === '{}');
ok('reject empty ref', JSON.stringify(unpackRef('')) === '{}');
ok('reject null ref', JSON.stringify(unpackRef(null)) === '{}');
ok('bad session id dropped', unpackRef('1234567890_1234567890-abc').sessionId === undefined);

// --- synthetic client id ---
const a = syntheticClientId('cs_test_123'), b2 = syntheticClientId('cs_test_123');
ok('synthetic id deterministic', a === b2, { a, b2 });
ok('synthetic id differs per session', syntheticClientId('cs_test_999') !== a);
ok('synthetic id shaped like a client id', /^\d+\.0$/.test(a), a);

// --- misc extractors ---
ok('quantity default 1', quantityFrom({}) === 1);
ok('quantity from metadata', quantityFrom({ metadata: { quantity: '3' } }) === 3);
ok('quantity rejects garbage', quantityFrom({ metadata: { quantity: 'x' } }) === 1);
ok('quantity rejects zero', quantityFrom({ metadata: { quantity: '0' } }) === 1);
ok('locale from metadata', localeFrom({ metadata: { edition: 'nl' } }) === 'nl');
ok('locale rejects junk', localeFrom({ metadata: { edition: 'de' } }) === '');
ok('locale absent', localeFrom({}) === '');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
