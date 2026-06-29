# Fix Anthropic Quota Selection Diagnostics

## Summary

Anthropic account selection can fail after the requested model quota prefilter removes otherwise-active accounts. Today that failure can look like a generic Claude pool exhaustion, which makes a correctly connected account appear unused or misrouted.

## Problem

Operators need to distinguish:

- no authenticated Anthropic accounts exist
- accounts exist but are globally unavailable
- active accounts exist, but the requested model quota key is cooling down

Without the model-quota detail in the selection error and CLI formatting, Droid/Claude smoke tests can be misread as hitting the wrong account or the wrong proxy.

## Proposed Change

Include model-quota prefilter diagnostics in Anthropic no-account errors, preserving retry metadata and the provider-boundary warning that OpenAI accounts cannot serve Claude routing. Preserve that detail in the Claude LB launcher friendly error output.
