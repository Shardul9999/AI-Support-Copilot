# Checkout and Payment Error Codes

When a customer reports an error code at checkout, look it up directly rather than guessing from the description alone — the same symptom (payment not going through) can map to different codes with different fixes.

- **TXN_4471** — Card issuer declined the transaction. Ask the customer to retry with a different payment method, or contact their card issuer directly; Acme cannot override a bank-side decline.
- **TXN_9987** — Payment gateway timeout. This is usually transient. Ask the customer to wait 10 minutes and retry; if it recurs on a second attempt, escalate to the payments team with the payment reference ID.
- **CHK_2210** — Cart total changed between page load and checkout (usually due to a price update or a coupon expiring mid-session). Ask the customer to refresh their cart and re-apply any coupon code before retrying.
- **ADDR_1103** — Delivery address failed serviceability check. The pincode entered is outside Acme's current delivery network; there is no override, and the order cannot be placed for that address.

If a customer reports a code not listed here, do not guess at its meaning — escalate with the exact code and any screenshot provided.
