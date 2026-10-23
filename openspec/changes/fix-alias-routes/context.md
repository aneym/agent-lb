# Context

The previous middleware recognized ten literal paths and only decoded JSON. Registered proxy endpoints now determine coverage, including compatibility rewrites and future routes. For example, a multipart audio request with model `voice` can resolve to `gpt-4o-transcribe`; a model targeting an excluded pattern returns HTTP 400 before dispatch.

Only model field byte ranges change in multipart bodies. Uploads, filenames, masks and other form fields retain their bytes. Requests without a model keep their handler defaults; warmup and backend transcription still control their own operation defaults.

Proof uses an isolated sqlite database and fake upstream, never the live service on port 2455.
