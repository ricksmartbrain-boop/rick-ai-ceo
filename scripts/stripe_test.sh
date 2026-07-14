#!/bin/bash
source ~/clawd/config/rick.env
# Test basic charges endpoint
curl -sv "https://api.stripe.com/v1/charges?limit=3" -u "${STRIPE_SECRET_KEY}:" 2>&1 | tail -20
