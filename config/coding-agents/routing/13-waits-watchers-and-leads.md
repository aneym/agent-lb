# Waits

No tool call blocks past 270 s; no gh poll loops. A `delegate_task` completion or a `t3_thread_send` wakes the parent. Otherwise wait with `t3_thread_wait` (bounded `timeoutMs`) for a launched thread, `task_status` for a delegated child, `seat-run --wait` under Monitor for a seat-run job, or a background shell or Monitor plus `t3_background_wait` for an outside fact (merge landed, deploy done).
