#!/bin/sh
# Zero-dependency test suite for the Stripe -> GA4 webhook. No npm install needed.
set -e
cd "$(dirname "$0")"
echo "--- signature verification ---"
node stripe-webhook.signature.test.js
echo "--- end to end ---"
node stripe-webhook.e2e.test.js 2>/dev/null
