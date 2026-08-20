# X-Forwarded-For: trust the right-hand end, not the left

Fixed 2026-08-20 in `backend/security.py::client_ip`. Applies to anything that keys a
per-client counter — rate limits, lockouts, quotas.

## The bug

`client_ip()` returned the **left-most** `X-Forwarded-For` hop, and everything in
`backend/security.py` keys off its return value: the general rate limit, the retrain
throttle, and the Basic Auth brute-force lockout.

A proxy **appends** the peer it saw to `X-Forwarded-For`. It does not replace the header.
So on a request that arrives at this app as:

```
X-Forwarded-For: 203.0.113.9, 10.0.0.7
                 ^ whatever the client typed   ^ what our proxy actually observed
```

...the left-most entry is the one value in the whole header that **nobody trustworthy wrote**.
Reading it meant an attacker got a fresh identity on every request just by varying a header
they control. Concretely, that gave away two things at once:

1. **The Basic Auth lockout protected nothing.** 15 failures per IP per 900s is a real
   constraint only if the attacker is stuck with one key. Rotating the forged first hop reset
   the counter every time, so unlimited password guessing was available to anyone who thought
   to send the header. The lockout still *looked* correct in tests and in the logs.
2. **The limiter became a memory-exhaustion vector.** Each unseen key allocated a dict entry
   and a deque that were never freed (see below), so the same rotation grew the table without
   bound.

## The fix

Count `LEADLENS_TRUSTED_PROXY_HOPS` (default **1**) back from the **right**. That lands on the
address our own proxy observed and wrote — the last entry a client cannot forge. Every
supported topology here (Cloudflare tunnel, Tailscale Serve, Render, Railway) is exactly one
hop, so the default is correct for all of them; raise it only when knowingly adding another
trusted proxy in front. With no header at all (local dev, direct connection) it falls back to
the socket peer, which is unspoofable.

Demonstrated after the fix: 12 requests carrying 12 different forged left-most hops collapse to
**1** limiter key and trip the lockout on schedule.

## The second bug in the same file

`_SlidingWindow.hit()` ended with:

```python
bucket.append(now)
allowed = len(bucket) <= limit
if not bucket:                 # never true -- we just appended
    self._events.pop(key, None)
```

The eviction was dead code, so the docstring's claim that "empty keys are dropped, so memory
tracks active clients rather than growing without bound" was false. Keys accumulated forever.
Now bounded two ways: `MAX_TRACKED_KEYS` (default 20,000, `LEADLENS_RATE_MAX_KEYS`) caps the
table with an amortised sweep of aged-out keys, and each key's own deque is trimmed to
`limit + 1` so being blocked costs a fixed amount of memory rather than one timestamp per
request in the flood.

## The general lesson

**A counter is only as good as the unforgeability of its key.** When reviewing any per-client
limit, the first question is not "is the limit low enough" but "can the caller choose which
bucket they land in". If they can, the limit is decoration. Note that both the vulnerable code
and its tests read as correct — `tests/test_security.py` had a passing test named
`test_prefers_left_most_forwarded_hop` that asserted the bug. It now asserts the fix, plus a
case that rotates 19 forged hops and requires them all to resolve to one key.

Related: [[Access-Control]].
