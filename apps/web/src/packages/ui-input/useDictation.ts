import { useEffect, useRef, useState } from 'react'
import { api } from '../connection/api'
import { toast } from '../store/store'

type Phase = 'idle' | 'requesting' | 'recording' | 'transcribing'
const MAX_RECORDING_MS = 60_000

/** Record only after a click, release the microphone on stop, and never send a message automatically. */
export function useDictation(chatId: string | null, insert: (text: string) => void) {
  const [phase, setPhase] = useState<Phase>('idle')
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const timer = useRef<number | null>(null)
  const generation = useRef(0)
  const informed = useRef(false)
  const insertRef = useRef(insert)
  insertRef.current = insert

  const release = (): void => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
    stream.current?.getTracks().forEach(track => track.stop())
    stream.current = null
  }

  useEffect(() => {
    setPhase('idle')
    return () => {
      generation.current++
      if (recorder.current) {
        recorder.current.onstop = null
        if (recorder.current.state !== 'inactive') recorder.current.stop()
        recorder.current = null
      }
      release()
    }
  }, [chatId])

  const toggle = async (): Promise<void> => {
    if (phase === 'recording') {
      recorder.current?.stop()
      return
    }
    if (phase !== 'idle') return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      toast('Microphone recording is not available here.', 'error')
      return
    }
    if (!informed.current) {
      if (!window.confirm('Dictation runs locally and is free. The first use downloads a multilingual Whisper model (about 500 MB). Your audio stays on this computer. Continue?')) return
      informed.current = true
    }
    const current = ++generation.current
    setPhase('requesting')
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      if (generation.current !== current) { media.getTracks().forEach(track => track.stop()); return }
      stream.current = media
      const recording = new MediaRecorder(media)
      recorder.current = recording
      const chunks: Blob[] = []
      recording.ondataavailable = e => { if (e.data.size) chunks.push(e.data) }
      recording.onerror = () => {
        generation.current++
        recording.onstop = null
        recorder.current = null
        release()
        setPhase('idle')
        toast('Microphone recording failed.', 'error')
      }
      recording.onstop = () => {
        recorder.current = null
        release()
        if (generation.current !== current) return
        setPhase('transcribing')
        void api.transcribe(new Blob(chunks, { type: recording.mimeType })).then(text => {
          if (generation.current !== current) return
          if (text) insertRef.current(text)
          else toast('No speech detected. Try again.')
        }).catch((error: unknown) => {
          if (generation.current === current) toast(error instanceof Error ? error.message : 'Dictation failed.', 'error')
        }).finally(() => { if (generation.current === current) setPhase('idle') })
      }
      recording.start()
      setPhase('recording')
      timer.current = window.setTimeout(() => { if (recording.state === 'recording') recording.stop() }, MAX_RECORDING_MS)
    } catch {
      release()
      if (generation.current === current) {
        setPhase('idle')
        toast('Could not access the microphone. Check your system permissions.', 'error')
      }
    }
  }
  return { phase, toggle }
}
