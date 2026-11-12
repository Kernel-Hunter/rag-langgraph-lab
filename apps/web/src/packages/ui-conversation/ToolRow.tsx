import { useState } from 'react'
import type { ApprovalAnswer } from '../connection/api'
import { respond, type ToolStep } from '../store/store'
import { Icon, type IconName } from '../ui-primitives/Icon'

const TOOLS: Record<string, { verb: string; icon: IconName; arg: string }> = {
  list_files: { verb: 'List', icon: 'folder', arg: 'path' },
  read_file: { verb: 'Read', icon: 'doc', arg: 'path' },
  glob: { verb: 'Find', icon: 'search', arg: 'pattern' },
  grep: { verb: 'Search', icon: 'search', arg: 'pattern' },
  search_docs: { verb: 'Search docs', icon: 'search', arg: 'query' },
  change_directory: { verb: 'Go to', icon: 'folder', arg: 'path' },
  write_file: { verb: 'Write', icon: 'edit', arg: 'path' },
  edit_file: { verb: 'Edit', icon: 'edit', arg: 'path' },
  run_command: { verb: 'Run', icon: 'terminal', arg: 'command' },
}

const short = (text: string, max: number): string => (text.length > max ? `${text.slice(0, max - 1)}…` : text)

/** One line under the tool name: what came back, in a few words. */
function summary(step: ToolStep): string {
  const out = step.output ?? ''
  if (step.approval?.answer === 'no' && /declined/i.test(out)) return 'Declined'
  if (!step.ok) return short(out.replace(/^Error:\s*/, ''), 140)
  const lines = out.split('\n').filter(Boolean)
  switch (step.name) {
    case 'read_file': return `${lines.length} line${lines.length === 1 ? '' : 's'}`
    case 'list_files':
    case 'glob': return out.startsWith('No ') ? short(out, 100) : `${lines.length} item${lines.length === 1 ? '' : 's'}`
    case 'grep': return out.startsWith('No ') ? short(out, 100) : `${lines.length} match${lines.length === 1 ? '' : 'es'}`
    case 'run_command': return short(lines[0] ?? 'done', 100)
    default: return short(lines[0] ?? 'done', 120)
  }
}

function Diff({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <pre className="diff" aria-label="Proposed change">
      {lines.map((line, i) => {
        const cls = line.startsWith('+++') || line.startsWith('---') ? 'meta'
          : line.startsWith('@@') ? 'hunk' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : ''
        return <span key={i} className={`ln ${cls}`}>{line || ' '}</span>
      })}
    </pre>
  )
}

function Approval({ step, chatId, msgId }: { step: ToolStep; chatId: string; msgId: string }) {
  const a = step.approval!
  const answer = (value: ApprovalAnswer): void => { void respond(chatId, msgId, a.id, value) }
  const waiting = !a.answer
  return (
    <div className={`approval${waiting ? ' waiting' : ''}`} role={waiting ? 'alertdialog' : undefined} aria-label="Approve this action">
      {a.kind === 'run'
        ? (
          <>
            <div className="approval-title"><Icon name="terminal" size={14} />Run a command{a.cwd ? <span className="dim"> in {a.cwd}</span> : null}</div>
            <pre className="diff cmd">$ {a.command}</pre>
          </>
        )
        : (
          <>
            <div className="approval-title"><Icon name="edit" size={14} />{step.name === 'write_file' ? 'Write' : 'Edit'} <code>{a.path}</code></div>
            {a.diff ? <Diff text={a.diff} /> : <p className="dim">No changes.</p>}
          </>
        )}
      {waiting
        ? (
          <div className="approval-actions">
            <button type="button" className="btn primary" disabled={a.sending} onClick={() => answer('yes')} autoFocus>Allow</button>
            {a.strict
              ? <span className="dim">Needs a yes every time.</span>
              : (
                <button type="button" className="btn" disabled={a.sending} onClick={() => answer('always')}
                  title={a.kind === 'run' ? 'Run commands without asking for the rest of this chat' : 'Edit files without asking for the rest of this chat'}>
                  Always allow {a.kind === 'run' ? 'commands' : 'edits'}
                </button>
              )}
            <button type="button" className="btn ghost danger" disabled={a.sending} onClick={() => answer('no')}>Deny</button>
          </div>
        )
        : <div className={`approval-result ${a.answer === 'no' ? 'bad' : 'ok'}`}>{a.answer === 'no' ? 'Denied' : a.answer === 'always' ? 'Allowed for this chat' : 'Allowed'}</div>}
    </div>
  )
}

export function ToolRow({ step, chatId, msgId }: { step: ToolStep; chatId: string; msgId: string }) {
  const [open, setOpen] = useState(false)
  const info = TOOLS[step.name] ?? { verb: step.name, icon: 'bolt' as IconName, arg: '' }
  const arg = info.arg ? String(step.args[info.arg] ?? '') : ''
  const running = step.output === undefined
  const waiting = !!step.approval && !step.approval.answer
  const state = running ? (waiting ? 'wait' : 'run') : step.ok ? 'ok' : 'bad'
  return (
    <div className={`tool ${state}`}>
      <button type="button" className="tool-head" onClick={() => setOpen(o => !o)} aria-expanded={open} disabled={running}>
        <span className="tool-dot" aria-hidden="true" />
        <Icon name={info.icon} size={14} />
        <span className="tool-verb">{info.verb}</span>
        {arg && <code className="tool-arg" title={arg}>{short(arg, 90)}</code>}
        {!running && <span className="tool-sum">{summary(step)}</span>}
        {running && !waiting && <span className="tool-sum shimmer">working</span>}
        {!running && step.output && <Icon name="chevron" size={13} className={`tool-chev${open ? ' rot' : ''}`} />}
      </button>
      {step.approval && (step.approval.answer === undefined || open) && <Approval step={step} chatId={chatId} msgId={msgId} />}
      {open && step.output && <pre className="tool-out">{short(step.output, 4000)}</pre>}
    </div>
  )
}
