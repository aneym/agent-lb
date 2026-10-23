# Balance exhaustion proof

The provider account-rule handler used a synthetic 503 as its last error status. That value entered the genuine transient-upstream classification introduced by fa6d81be. Clear the status after a rule consumes an upstream error; the fallback status remains 503 while the code and retry timestamp come from pool eligibility. This also clears a previous transient status when a subsequent attempt ends in an account rule.

Current main moved balance handling into configurable rules. The correction lives there, leaving genuine transient responses unchanged. It changes no account ownership, settlement, payload or retry-budget behavior.

Run `.venv/bin/python scripts/proof_balance_503.py --out <fresh-writable-directory>`. The proof starts real local HTTP servers, disposable SQLite databases and fake provider endpoints; it never calls the live service. It checks GLM and Kimi separately: four balance failures return the provider no-available-accounts error, a 503 followed by success uses a different account, and four genuine 503 failures retain api_error. The baseline returned api_error on the balance scenario; the correction passes all six scenarios.

Paused GLM and Kimi accounts have no timed reset, so these balance scenarios exercise eligibility.next_reset_at=None. A future reset and Anthropic streaming pool-wait hold are not directly exercised by this proof. Their existing selection and exhaustion code is unchanged.
