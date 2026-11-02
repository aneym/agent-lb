export function registerCompaction(on: any, sessions: Map<string, any>) {
  on('session.compact', async ($: any, event: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state || event.agentId !== undefined || event.trigger === 'precompute') return next(event)
    state.emit('compaction.start', { trigger: event.trigger === 'auto' ? 'auto' : 'manual', at: new Date().toISOString() })
    let ok = false
    try {
      const result = await next(event)
      ok = result?.skip === undefined
      return result
    } finally {
      state.emit('compaction.done', { at: new Date().toISOString(), ok })
    }
  })
}
