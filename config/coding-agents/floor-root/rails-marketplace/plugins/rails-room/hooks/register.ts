import { registerCompaction } from './compaction'
import { createIntake } from './intake'
import type { Register } from 'claude-code'

const NAMES = { thinking: 'thinking', agentStarted: 'agent.started', agentDone: 'agent.done', context: 'context', rateLimit: 'rate_limit' }

const sessions = new Map<string, any>()
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const MAX_IMAGES = 10
const MAX_IMAGE_CHARS = 3 * 1024 * 1024
const BATCH_BYTES = 768 * 1024

export function imagesOf(value: any, found: { media_type: string; data: string }[] = [], depth = 0): { media_type: string; data: string }[] {
  if (!value || typeof value !== 'object' || depth > 6 || found.length >= MAX_IMAGES) return found
  if (Array.isArray(value)) { for (const item of value) imagesOf(item, found, depth + 1); return found }
  if (value.type === 'image') {
    const media = value.file?.type ?? value.mimeType ?? value.source?.media_type ?? value.media_type
    const data = value.file?.base64 ?? value.data ?? value.source?.data
    if (IMAGE_TYPES.includes(media) && typeof data === 'string' && data && data.length <= MAX_IMAGE_CHARS) found.push({ media_type: media, data })
    return found
  }
  for (const key of ['content', 'result', 'output']) if (key in value) imagesOf(value[key], found, depth + 1)
  return found
}
export const register: Register = (on: any) => {
  registerCompaction(on, sessions)
  on('session.start', async ($: any, e: any, next: any) => {
    const socket = await $.env.get('RAILS_ROOM_SOCKET')
    const token = await $.env.get('RAILS_ROOM_TOKEN')
    const session = await $.env.get('RAILS_ROOM_SESSION')
    if (!socket || !token || !session) return next(e)
    let active = true, connected = false, resumed = false, busy = false, delay = 100, retryAt = 0
    let turnId = '', commandId: string | null = null, dropped = 0
    const pending: any[] = [], prompts: any[] = [], agents = new Map(), asks = new Map(), launches = new Map<string, string>()
    const streamed = new Map<string, string[]>()
    const thoughts = new Map<string, number>()
    const hello = { pid: Number((await $.process.run(['sh', '-c', 'echo "$PPID"'])).stdout?.trim()) || 0,
      harnessSessionId: await $.session.id(), version: (await $.session.version()).version }
    const request = async (path: string, body?: any) => {
      const r = await $.http.fetch('http://bridge' + path, { socketPath: socket,
        headers: { 'x-rails-room-token': token }, method: body === undefined ? 'GET' : 'POST',
        body: body === undefined ? undefined : JSON.stringify(body) })
      if (!r.ok) throw new Error('bridge unavailable')
      return JSON.parse(r.text)
    }
    const sent = new WeakSet<object>()
    const emit = (type: string, data: any = {}) => {
      const last = pending[pending.length - 1]
      if (type === 'text.delta' && last?.type === type && last.streamId === data.streamId && !sent.has(last)) {
        last.text += data.text
        return
      }
      pending.push({ type, ...data })
      if (pending.length > 2000) { pending.shift(); dropped++ }
    }
    const tag = (ev: any) => ({ turnId: ev.agentId ? (agents.get(ev.agentId) ?? turnId) : (turnId || ev.turnId || ''),
      ...(ev.agentId ? { agentId: ev.agentId } : {}) })
    const usage = (u: any = {}) => ({ model: u.model, inputTokens: u.input_tokens ?? 0, outputTokens: u.output_tokens ?? 0,
      cacheReadTokens: u.cache_read_input_tokens ?? 0, cacheWriteTokens: u.cache_creation_input_tokens ?? 0 })
    const intake = createIntake($, emit, prompts, asks, () => ({ turnId, commandId }))
    const fail = () => { connected = false; retryAt = Date.now() + delay; delay = Math.min(2000, delay * 2) }
    let sending = false, polling = false
    const send = async () => {
      if (sending) return
      sending = true
      try {
        if (dropped) { const n = dropped; await request('/v0/events', { events: [{ type: 'error', code: 'events_dropped', count: n, message: 'Buffered events dropped' }] }); dropped -= n }
        while (pending.length && connected) {
          const batch: any[] = []
          let bytes = 0
          for (const item of pending.slice(0, 50)) {
            const size = item.images ? JSON.stringify(item).length : 0
            if (batch.length && bytes + size > BATCH_BYTES) break
            batch.push(item); bytes += size
          }
          for (const item of batch) sent.add(item)
          await request('/v0/events', { events: batch })
          for (const item of batch) { const i = pending.indexOf(item); if (i >= 0) pending.splice(i, 1) }
        }
      } catch { fail() } finally { sending = false }
    }
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const { commands = [] } = await request('/v0/commands?wait=1000')
        for (const c of commands) await intake.take(c)
        delay = 100; retryAt = 0
      } catch { fail() } finally { polling = false }
    }
    const pump = async () => {
      if (!active || busy || Date.now() < retryAt) return
      if (!connected) {
        if (sending || polling) return
        busy = true
        try { await request('/v0/hello', { ...hello, resumed }); connected = true; resumed = true }
        catch { fail() } finally { busy = false }
        if (!connected) return
      }
      void send(); void poll()
    }
    const timer = $.clock.every(25, pump)
    sessions.set(hello.harnessSessionId, {
      emit, tag, usage, asks, agents, prompts, intake, timer, streamed, thoughts, launches, request, ended: '',
      get active() { return active }, set active(value) { active = value },
      get turnId() { return turnId }, set turnId(value) { turnId = value },
      get commandId() { return commandId }, set commandId(value) { commandId = value },
    })
    void pump()
    return next(e)
  })

  on('session.end', async ($: any, e: any, next: any) => {
    const id = await $.session.id(), state = sessions.get(id)
    if (state) { state.active = false; state.timer.cancel(); sessions.delete(id) }
    return next(e)
  })
  on('turn.start', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state || ev.agentId !== undefined) return next(ev)
    const { emit, prompts } = state
    const external = typeof ev.text === 'string' && /^\s*<task-notification>/.test(ev.text)
    state.streamed.delete(state.ended); state.ended = ''
    state.turnId = ev.turnId; state.commandId = external ? null : prompts.shift()?.id ?? null
    state.intake.started(state.commandId, state.turnId)
    emit('turn.start', { turnId: state.turnId, commandId: state.commandId, ...(external ? { external: true } : {}) }); return next(ev)
  })
  on('session.append', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit, tag, streamed, thoughts } = state
    const open = state.turnId, at = tag(ev)
    const r = await next(ev), message = r?.message ?? ev.message
    const blocks = Array.isArray(message?.content) ? message.content : []
    const loop = ev.agentId ?? ''
    for (const b of blocks) if (b.type === 'thinking') {
      const live = thoughts.get(loop) ?? 0
      if (live > 0) { thoughts.set(loop, live - 1); continue }
      emit(NAMES.thinking, { ...at, state: 'start' })
      emit(NAMES.thinking, { ...at, state: 'stop' })
    }
    const role = message?.role ?? message?.type
    const meta = message?.isMeta === true || r?.isMeta === true || ev?.isMeta === true
    const content = typeof message?.content === 'string' ? [{ type: 'text', text: message.content }] : blocks
    const texts = content.filter((b: any) => b.type === 'text')
    if (role === 'user') {
      const notice = texts.map((b: any) => b.text).join('\n')
      if (/^\s*<task-notification>/.test(notice)) {
        const task = notice.match(/<task-id>([^<]+)<\/task-id>/)?.[1]
        const status = notice.match(/<status>([^<]+)<\/status>/)?.[1]
        if (task && status) emit('system', { subtype: 'task_notification', task_id: task, status })
      }
    }
    const late = !open && role === 'assistant' && !ev.agentId ? state.ended : ''
    const where = late ? { ...at, turnId: late } : at
    const ids = role === 'assistant' && !ev.agentId ? streamed.get(where.turnId) ?? [] : []
    const rows = ids.length ? texts.filter((b: any) => b.text).map((b: any) => ({ text: b.text, streamId: ids.shift() }))
      : [{ text: texts.map((b: any) => b.text).join('\n'), streamId: undefined }]
    for (const { text, streamId } of rows) {
      if (text && (open || late)) emit('row', { ...where, role, text, uuid: r?.uuid ?? ev.uuid, ...(streamId ? { streamId } : {}), ...(meta ? { isMeta: true } : {}) })
    }
    return r
  })
  on('tool.call', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit, tag, launches } = state
    const { tool, tool_use_id, agentId, ...input } = ev
    const attribution = tag(ev)
    if (attribution.turnId) {
      launches.set(tool_use_id, attribution.turnId)
      if (launches.size > 500) launches.delete(launches.keys().next().value)
    }
    emit('tool.call', { ...attribution, toolUseId: tool_use_id, tool, input })
    const r = await next(ev)
    const images = r?.deny ? [] : imagesOf(r?.result)
    if (tool === 'Bash' && typeof r?.result?.backgroundTaskId === 'string')
      emit('system', { subtype: 'task_started', task_id: r.result.backgroundTaskId, task_type: 'local_bash', tool_use_id, description: input.description ?? 'Background shell', is_backgrounded: true, turnId: attribution.turnId })
    emit('tool.result', { ...attribution, toolUseId: tool_use_id, isError: !!(r?.isError || r?.deny), text: r?.text ?? r?.deny ?? '', ...(images.length ? { images } : {}) })
    return r
  })
  on('tool.check', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit, asks, request } = state
    const r = await next(ev)
    if (r?.decision !== 'ask') return r
    const askId = ev.tool_use_id, held: any = { answer: null }; asks.set(askId, held)
    emit('ask', { askId, toolUseId: askId, tool: ev.tool, input: ev.input, reason: r.reason ?? '' })
    try {
      while (!held.answer && !next.signal.aborted && state.active) {
        const woke = new Promise<void>(resolve => { held.wake = resolve })
        await Promise.race([woke, request('/v0/hold?wait=1000').catch(() => $.clock.sleep(250))])
      }
      const a = held.answer ?? { decision: 'deny', by: 'interrupt' }
      emit('ask.resolved', { askId, decision: a.decision, by: a.by ?? 'host' })
      return { decision: a.decision, reason: 'Room answer' }
    } finally { asks.delete(askId) }
  })
  on('agent.spawn', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit, tag, agents, launches } = state
    const parent = launches.get(ev.tool_use_id) ?? agents.get(ev.parentAgentId) ?? tag(ev).turnId, r = await next(ev)
    if (r?.agentId) {
      agents.set(r.agentId, parent)
      emit(NAMES.agentStarted, { turnId: parent, agentId: r.agentId, toolUseId: ev.tool_use_id,
        subagentType: ev.subagentType, model: r.model ?? ev.model ?? ev.parentModel, description: ev.description })
    }
    return r
  })
  on('turn.step', async function* ($: any, ev: any, next: any) {
    const state = sessions.get(await $.session.id())
    if (!state) return yield* next(ev)
    const { emit, tag, usage, streamed, thoughts } = state
    const where = tag(ev), loop = ev.agentId ?? ''
    if (!ev.agentId) emit('run.phase', { ...where, phase: { id: 'model_wait', label: 'Waiting for model' } })
    let summary = ''
    let thinking: unknown = null, block: unknown = null, live: { id: string } | null = null
    const open = (index: number) => {
      const id = `${where.turnId}:${ev.index}:${index}`
      streamed.set(where.turnId, [...(streamed.get(where.turnId) ?? []), id])
      return { id }
    }
    const close = () => { live = null }
    const stopThinking = () => { if (thinking !== null) { thinking = null; emit(NAMES.thinking, { ...where, state: 'stop' }) } }
    const watch = (c: any) => {
      if (c?.kind === 'thinking') {
        if (thinking === null || c.index !== thinking) {
          stopThinking(); thinking = c.index
          summary = ''; thoughts.set(loop, (thoughts.get(loop) ?? 0) + 1); emit(NAMES.thinking, { ...where, state: 'start' })
        }
        if (typeof c.text === 'string' && c.text) {
          summary = (summary + c.text).replace(/\s+/g, ' ').trim().slice(-200)
          emit(NAMES.thinking, { ...where, state: 'start', summary })
        }
      } else if (c?.kind && c.kind !== 'engine') stopThinking()
      if (c?.kind === 'text' && !ev.agentId && typeof c.text === 'string' && c.text) {
        if (!live || c.index !== block) { close(); block = c.index; live = open(Number(c.index) || 0) }
        emit('text.delta', { ...where, streamId: live.id, text: c.text })
      }
    }
    const stream = next(ev)
    let finished = false
    try {
      for (;;) {
        const step = await stream.next()
        if (step.done) { finished = true; const r = step.value; if (r?.usage) emit('usage', { ...where, ...usage({ model: ev.model, ...r.usage }) }); return r }
        try { watch(step.value) } catch {}
        yield step.value
      }
    } catch (error: any) {
      finished = true
      if (/rate.?limit|usage.?limit|overloaded/i.test(String(error?.code) + String(error?.message)))
        emit('error', { code: NAMES.rateLimit, retryAt: error.retryAt, message: String(error.message ?? 'Usage limit') })
      throw error
    } finally {
      close(); stopThinking()
      if (!finished) await stream.return?.(undefined)
    }
  })
  on('classic.Stop', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (state && Array.isArray(ev.background_tasks))
      state.emit('system', { subtype: 'background_tasks_changed', tasks: ev.background_tasks.map((task: any) => ({ task_id: task.id, task_type: task.type, description: task.description, status: task.status, is_backgrounded: true })) })
    return next(ev)
  })
  on('turn.complete', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit, tag, agents } = state
    const reason = ev.isAborted ? 'aborted' : ev.reason === 'error' ? 'error' : 'completed'
    if (ev.agentId) { emit(NAMES.agentDone, { ...tag(ev), reason, usage: ev.usage }); agents.delete(ev.agentId) }
    else {
      const done = state.turnId || ev.turnId
      emit('turn.complete', { turnId: done, reason }); state.intake.finished(state.commandId, done, reason)
      state.turnId = ''; state.commandId = null
      state.streamed.delete(state.ended); state.ended = done
      if (ev.isAborted) {
        try { await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: '[Request interrupted by user]' }] } }) } catch {}
      }
    }
    return next(ev)
  })
  on('session.measure', async ($: any, ev: any, next: any) => {
    const state = sessions.get(await $.session.id())
    if (!state) return next(ev)
    const { emit } = state
    const c = ev.context ?? ev
    emit(NAMES.context, { tokens: c.tokens, window: c.window, percent: c.percent }); return next(ev)
  })
}
