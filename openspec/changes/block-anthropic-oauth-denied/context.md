The scope is Anthropic selection and organization OAuth refusal. Canceled subscriptions already leave the routable pool. Quota recovery promotes snapshot-derived quota statuses to active, so weekly exhaustion needs a hard gate before ranking.

The refusal uses the existing deactivation reason field while leaving account status active. Subscription usability checks this marker, and successful usage refresh clears it with a compare-and-set update. This lets normal refresh continue and avoids writing a false cancellation into the subscription ledger.

For example, if A receives the organization denial and B is usable, the messages retry serves through B. Later requests exclude A even with sticky affinity. A successful usage refresh can admit A again; failed refresh and unrelated refusals cannot clear the marker. System blocks and upstream request payloads retain the existing behavior.
