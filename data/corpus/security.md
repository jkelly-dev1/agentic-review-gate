---
id: SEC-STD
title: Secure Coding Standard
---
All external input crossing a trust boundary must be validated and have an
associated automated test. Secrets must never be logged or committed. Webhook
endpoints must verify HMAC signatures and reject unsigned or replayed requests.
Authentication and authorization checks must be enforced server-side.
