const NOTE_ABOVE = '<system-reminder>The note above arrived since your last turn. Act on it now.</system-reminder>'
const KEEP = 500
const mark = (id: string) => `<system-reminder>rails-note:${id}</system-reminder>`

type Taken = { turnId?: string; reason?: string }
type Noted = { d: string; u?: string }

const digest = (text: string) => {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619)
  return (h >>> 0).toString(36) + ':' + text.length
}

export function createIntake($: any, emit: (type: string, data?: any) => void, prompts: any[], asks: Map<string, any>,
  current: () => { turnId: string; commandId: string | null }) {
  const taken = new Map<string, Taken>()
  const remember = <T>(map: Map<string, T>, id: string, value: T) => {
    map.delete(id)
    map.set(id, value)
    if (map.size > KEEP) map.delete(map.keys().next().value as string)
  }
  const key = $.session.id().then((id: string) => 'intake:noted:' + id)
  let book: Promise<Map<string, Noted>> | null = null
  const noted = () => book ??= (async () => {
    try {
      return new Map<string, Noted>(JSON.parse(String((await $.store.get(await key)) ?? '[]'))
        .map(([id, v]: [string, any]) => [id, typeof v === 'string' ? { d: v } : v]))
    } catch { return new Map<string, Noted>() }
  })()
  const save = async (ids: Map<string, Noted>) => $.store.set(await key, JSON.stringify([...ids]))
  const placed = async (c: any) => {
    const ids = await noted(), had = ids.get(c.id), d = digest(c.text)
    if (had?.u && had.u !== 'deny') return had
    if (!(await $.session.messages()).some((m: any) => m.role === 'user' && typeof m.text === 'string' && m.text.endsWith(mark(c.id)))) return had
    remember(ids, c.id, { d, u: 'seen' })
    await save(ids)
    return ids.get(c.id)
  }
  const note = async (c: any) => {
    const ids = await noted(), d = digest(c.text)
    const at = taken.has(c.id) ? 'taken' : (await placed(c))?.u
    if (!at || at === 'deny') {
      remember(ids, c.id, { d })
      await save(ids)
      const r = await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: c.text }, { type: 'text', text: mark(c.id) }] } })
      if (typeof r?.uuid !== 'string') return emit('error', { code: 'note_denied', message: (typeof r?.deny === 'string' ? r.deny : 'Note append was refused').slice(0, 200) })
      remember(ids, c.id, { d, u: r.uuid })
      await save(ids)
    }
    emit('note.appended', { id: c.id })
  }
  const prompt = async (c: any) => {
    const seen = taken.get(c.id)
    if (seen) return emit('prompt.seen', { id: c.id, ...seen })
    remember(taken, c.id, {})
    prompts.push(c)
    const forget = () => {
      taken.delete(c.id)
      const i = prompts.indexOf(c)
      if (i >= 0) prompts.splice(i, 1)
    }
    const had = await placed(c)
    const above = Boolean(had?.u && had.u !== 'deny' && had.d === digest(c.text))
    void $.prompt.submit({ text: above ? NOTE_ABOVE : c.text, asUser: true }).then((r: any) => {
      if (typeof r?.drop !== 'string') return
      forget()
      emit('prompt.dropped', { id: c.id, reason: r.drop.slice(0, 200) })
    }, () => { forget(); emit('error', { code: 'prompt_failed', message: 'Prompt submission failed' }) })
  }
  return {
    async take(c: any) {
      if (c.kind === 'prompt') await prompt(c)
      else if (c.kind === 'note') await note(c)
      else if (c.kind === 'interrupt') {
        const { turnId, commandId } = current()
        if (turnId && c.commandId === commandId) await $.turn.abort({ turnId })
      } else if (c.kind === 'answer') {
        const held = asks.get(c.askId)
        if (held && !held.answer && ['allow', 'deny'].includes(c.decision)) { held.answer = c; held.wake?.() }
      }
    },
    started(commandId: string | null, turnId: string) {
      if (commandId && taken.has(commandId)) remember(taken, commandId, { turnId })
    },
    finished(commandId: string | null, turnId: string, reason: string) {
      if (commandId && taken.has(commandId)) remember(taken, commandId, { turnId, reason })
    },
  }
}
