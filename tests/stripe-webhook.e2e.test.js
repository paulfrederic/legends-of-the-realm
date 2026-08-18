process.env.STRIPE_WEBHOOK_SECRET='whsec_e2e_secret';
process.env.GA4_MEASUREMENT_ID='G-31WLZW9T5Q';
process.env.GA4_API_SECRET='fake_api_secret';
const crypto=require('crypto');
const handler=require(require('path').join(__dirname,'../api/stripe-webhook.js'));

let captured=null;
global.fetch=async(url,opts)=>{ captured={url,body:JSON.parse(opts.body)}; return {status:204,text:async()=>''}; };

function mkReq(bodyObj,{sign=true,secret='whsec_e2e_secret',method='POST'}={}){
  const raw=Buffer.from(JSON.stringify(bodyObj),'utf8');
  const ts=Math.floor(Date.now()/1000);
  const sig=crypto.createHmac('sha256',secret)
    .update(Buffer.concat([Buffer.from(ts+'.','utf8'),raw])).digest('hex');
  const req={method,headers:{},body:raw};
  if(sign) req.headers['stripe-signature']=`t=${ts},v1=${sig}`;
  return req;
}
function mkRes(){const r={code:null,payload:null};
  r.status=c=>{r.code=c;return r;}; r.json=p=>{r.payload=p;return r;};
  r.setHeader=()=>{}; return r;}

const EVT=(over={})=>({id:'evt_1',type:'checkout.session.completed',
  data:{object:Object.assign({
    id:'cs_test_a1b2c3', payment_status:'paid', amount_total:3995, currency:'eur',
    client_reference_id:'1234567890_1755400000-1755500000',
    metadata:{edition:'nl',quantity:'1'}
  },over)}});

let pass=0,fail=0;
const ok=(n,c,x)=>{c?pass++:(fail++,console.log('  FAIL:',n,x===undefined?'':JSON.stringify(x,null,1)));};

(async()=>{
  // 1. happy path
  captured=null; let res=mkRes();
  await handler(mkReq(EVT()),res);
  ok('200 on valid signed event',res.code===200,res);
  ok('called GA4 mp/collect',captured&&captured.url.includes('/mp/collect'),captured&&captured.url);
  ok('measurement_id in url',captured.url.includes('measurement_id=G-31WLZW9T5Q'));
  ok('api_secret in url',captured.url.includes('api_secret=fake_api_secret'));
  const b=captured.body, e=b.events[0], it=e.params.items[0];
  ok('client_id stitched',b.client_id==='1234567890.1755400000',b.client_id);
  ok('session_id stitched',e.params.session_id==='1755500000',e.params.session_id);
  ok('event name purchase',e.name==='purchase');
  ok('value in major units',e.params.value===39.95,e.params.value);
  ok('currency upper',e.params.currency==='EUR');
  ok('transaction_id is stripe session',e.params.transaction_id==='cs_test_a1b2c3');
  ok('stitched flag yes',e.params.stitched==='yes');
  ok('item sku matches schema',it.item_id==='LOTR-FE-78');
  ok('item variant from metadata',it.item_variant==='nl');
  ok('quantity 1',it.quantity===1);
  ok('unit price',it.price===39.95,it.price);
  ok('engagement_time_msec present',e.params.engagement_time_msec===1);

  // 2. forged signature
  captured=null; res=mkRes();
  await handler(mkReq(EVT(),{secret:'whsec_wrong'}),res);
  ok('400 on forged signature',res.code===400,res);
  ok('no GA4 call on forged signature',captured===null);

  // 3. unsigned
  captured=null; res=mkRes();
  await handler(mkReq(EVT(),{sign:false}),res);
  ok('400 on unsigned',res.code===400);
  ok('no GA4 call on unsigned',captured===null);

  // 4. GET
  res=mkRes(); await handler(mkReq(EVT(),{method:'GET'}),res);
  ok('405 on GET',res.code===405);

  // 5. irrelevant event type acknowledged, not retried
  captured=null; res=mkRes();
  await handler(mkReq({id:'evt_2',type:'payment_intent.created',data:{object:{}}}),res);
  ok('200 ignoring other event types',res.code===200&&res.payload.ignored==='payment_intent.created',res.payload);
  ok('no GA4 call for other types',captured===null);

  // 6. unpaid session must not count as revenue
  captured=null; res=mkRes();
  await handler(mkReq(EVT({payment_status:'unpaid'})),res);
  ok('200 ignoring unpaid session',res.code===200&&res.payload.ignored==='unpaid session',res.payload);
  ok('no GA4 call for unpaid',captured===null);

  // 7. consent declined -> no ref -> synthetic id, revenue still counted
  captured=null; res=mkRes();
  await handler(mkReq(EVT({client_reference_id:null})),res);
  ok('200 without client_reference_id',res.code===200);
  ok('synthetic client_id used',/^\d+\.0$/.test(captured.body.client_id),captured.body.client_id);
  ok('marked unstitched',captured.body.events[0].params.stitched==='no');
  ok('revenue still counted',captured.body.events[0].params.value===39.95);
  ok('no session_id when unstitched',captured.body.events[0].params.session_id===undefined);

  // 8. quantity > 1 splits unit price
  captured=null; res=mkRes();
  await handler(mkReq(EVT({amount_total:11985,metadata:{edition:'fr',quantity:'3'}})),res);
  ok('total value for qty 3',captured.body.events[0].params.value===119.85);
  ok('unit price divided',captured.body.events[0].params.items[0].price===39.95,
     captured.body.events[0].params.items[0].price);
  ok('quantity 3',captured.body.events[0].params.items[0].quantity===3);

  // 9. GA4 outage must not fail the webhook (Stripe would retry a good payment)
  global.fetch=async()=>{throw new Error('network down');};
  res=mkRes(); await handler(mkReq(EVT()),res);
  ok('200 even when GA4 is down',res.code===200,res);

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail?1:0);
})();
