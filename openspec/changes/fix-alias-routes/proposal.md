# Apply model policy across proxy routes

Alias resolution and excluded-model refusal currently skip multipart requests and several proxy routes. Derive coverage from registered proxy routes and inspect JSON, URL-encoded forms and multipart model fields before dispatch. Preserve file bytes and existing error envelopes.
