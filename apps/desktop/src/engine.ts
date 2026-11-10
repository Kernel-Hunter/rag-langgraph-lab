// The engine is the Python backend (src/vesper) running as a child process.
// It prints one `VESPER_READY {"port": ..., "token": "..."}` line on stdout
// once its local server listens, and exits when its stdin closes, so it never
// outlives the app even if Electron itself crashes.

import { spawn, type ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { createServer } from 'node:net'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { app } from 'electron'

const READY_PREFIX = 'VESPER_READY '
const READY_TIMEOUT_MS = 60_000
const STOP_GRACE_MS = 3_000
const LOG_LINES = 40
// The UI keeps chats in localStorage, which is per origin (host + port). A stable
// port keeps history across launches; a random one is the fallback.
const PREFERRED_PORTS = [47321, 47322, 47323, 47324, 47325]
const STABLE_UPTIME_MS = 60_000

function portFree(port: number): Promise<boolean> {
  return new Promise((done) => {
    const srv = createServer()
    srv.once('error', () => done(false))
    srv.listen(port, '127.0.0.1', () => srv.close(() => done(true)))
  })
}

async function pickPort(): Promise<number | undefined> {
  for (const port of PREFERRED_PORTS) if (await portFree(port)) return port
  return undefined
}

export interface EngineInfo {
  readonly port: number
  readonly token: string
  readonly url: string
  readonly origin: string
}

interface EngineCommand {
  readonly file: string
  readonly args: string[]
  readonly cwd?: string
}

/** Bundled engine in a packaged app, the repo's virtualenv in development. */
export function engineCommand(): EngineCommand {
  const windows = process.platform === 'win32'
  if (app.isPackaged) {
    const dir = join(process.resourcesPath, 'engine')
    return { file: join(dir, windows ? 'vesper-engine.exe' : 'vesper-engine'), args: ['--engine'], cwd: dir }
  }
  const repo = resolve(app.getAppPath(), '..', '..')
  const python = windows ? join(repo, '.venv', 'Scripts', 'python.exe') : join(repo, '.venv', 'bin', 'python')
  return { file: python, args: ['-m', 'vesper.desktop', '--engine'], cwd: repo }
}

export class EngineError extends Error {
  constructor(message: string, readonly log: string) {
    super(message)
  }
}

/**
 * Starts the engine, restarts it once if it crashes after a successful start,
 * and stops it on demand.
 *
 * Events: 'restarted' (EngineInfo) after a crash recovery, 'failed' (EngineError)
 * when it crashed again or the restart did not come up.
 */
export class Engine extends EventEmitter {
  private child: ChildProcess | undefined
  private stopping = false
  private restarts = 0
  private startedAt = 0
  private log: string[] = []
  info: EngineInfo | undefined

  constructor(private readonly env: NodeJS.ProcessEnv = process.env) {
    super()
  }

  async start(): Promise<EngineInfo> {
    const port = this.env.VESPER_PORT ? undefined : await pickPort()
    try {
      return await this.launch(port)
    } catch (err) {
      if (port === undefined || this.stopping) throw err
      return this.launch(undefined) // preferred port got taken in between: use any free one
    }
  }

  private async launch(port: number | undefined): Promise<EngineInfo> {
    this.stopping = false
    const cmd = engineCommand()
    if (!existsSync(cmd.file)) {
      throw new EngineError(`The Vesper engine was not found at ${cmd.file}.`, '')
    }
    const child = spawn(cmd.file, cmd.args, {
      cwd: cmd.cwd,
      env: { ...this.env, ...(port ? { VESPER_PORT: String(port) } : {}), PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    this.child = child
    this.log = []
    child.stdin?.on('error', () => {}) // EPIPE when the engine is already gone
    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', (chunk: string) => {
      if (!app.isPackaged) process.stderr.write(chunk)
      this.remember(chunk)
    })

    const info = await new Promise<EngineInfo>((resolveReady, rejectReady) => {
      let settled = false
      const fail = (message: string): void => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        rejectReady(new EngineError(message, this.log.join('\n')))
      }
      const timer = setTimeout(() => {
        fail('The Vesper engine did not start within 60 seconds.')
        child.kill()
      }, READY_TIMEOUT_MS)
      child.once('error', err => fail(`The Vesper engine could not be started: ${err.message}`))
      child.once('exit', (code, signal) => fail(`The Vesper engine stopped while starting (${signal ?? `exit code ${code}`}).`))
      const lines = createInterface({ input: child.stdout! })
      lines.on('line', (line) => {
        if (settled || !line.startsWith(READY_PREFIX)) {
          if (!app.isPackaged) process.stdout.write(`[engine] ${line}\n`)
          this.remember(line)
          return
        }
        try {
          const ready = JSON.parse(line.slice(READY_PREFIX.length)) as { port?: unknown, token?: unknown }
          if (!Number.isInteger(ready.port) || typeof ready.token !== 'string') throw new Error('bad fields')
          const port = ready.port as number
          const origin = `http://127.0.0.1:${port}`
          settled = true
          clearTimeout(timer)
          resolveReady({ port, token: ready.token, origin, url: `${origin}/#token=${encodeURIComponent(ready.token)}` })
        } catch {
          fail(`The Vesper engine sent an unreadable start signal: ${line}`)
          child.kill()
        }
      })
    })

    this.info = info
    this.startedAt = Date.now()
    child.once('exit', (code, signal) => {
      if (this.child !== child) return
      this.child = undefined
      if (this.stopping) return
      void this.recover(`The Vesper engine stopped unexpectedly (${signal ?? `exit code ${code}`}).`)
    })
    return info
  }

  private async recover(reason: string): Promise<void> {
    if (Date.now() - this.startedAt > STABLE_UPTIME_MS) this.restarts = 0 // ran fine for a while: allow another restart
    if (this.restarts >= 1) {
      this.emit('failed', new EngineError(reason, this.log.join('\n')))
      return
    }
    this.restarts += 1
    try {
      this.emit('restarted', await this.start())
    } catch (err) {
      this.emit('failed', err instanceof EngineError ? err : new EngineError(String(err), this.log.join('\n')))
    }
  }

  /** Close stdin (the engine exits on its own), then kill it if it lingers. */
  stop(): Promise<void> {
    this.stopping = true
    const child = this.child
    this.child = undefined
    if (child === undefined || child.exitCode !== null || child.signalCode !== null) return Promise.resolve()
    return new Promise((done) => {
      const timer = setTimeout(() => {
        child.kill()
        done()
      }, STOP_GRACE_MS)
      child.once('exit', () => {
        clearTimeout(timer)
        done()
      })
      child.stdin?.end()
    })
  }

  private remember(text: string): void {
    for (const line of text.split(/\r?\n/)) {
      if (line.trim() === '') continue
      this.log.push(line)
    }
    if (this.log.length > LOG_LINES) this.log.splice(0, this.log.length - LOG_LINES)
  }
}
