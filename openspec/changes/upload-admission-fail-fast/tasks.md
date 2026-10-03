## 1. Fail fast

- [x] 1.1 Estimate projected wait before queueing and raise `UploadAdmissionRejected` when it exceeds the ceiling.
- [x] 1.2 Keep the first waiter on an empty queue admitted when outstanding bytes are zero.
- [x] 1.3 Default `AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS` to 30.
- [x] 1.4 Attach rate, backlog, projected wait, ceiling, class, and waited seconds to the timeout.

## 2. Report

- [x] 2.1 Log one warning for both failures with class, rate, backlog, seconds, limit, and hashed session.
- [x] 2.2 Persist `upload_admission_rejected` or `upload_admission_timeout` and return HTTP 503 with that code.

## 3. Proof

- [x] 3.1 Reject a hopeless second admit in under 0.5s and keep an eligible waiter on the timeout path.
- [x] 3.2 Map a rejection through the Anthropic count-tokens handler onto a request log and a 503.
