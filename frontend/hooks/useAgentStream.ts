// hooks/useAgentStream.ts
// Manages the SSE connection and parses events into the correct shape

"use client"

import { useState, useCallback, useRef } from "react"
import { startRun, resolveStreamUrl, refreshStreamToken } from "@/lib/api"
import { SSEEvent, isStepEvent } from "@/lib/types"

type Status = "idle" | "running" | "complete" | "error"

export function useAgentStream() {
    const [steps, setSteps] = useState<SSEEvent[]>([])
    const [status, setStatus] = useState<Status>("idle")
    const [runId, setRunId] = useState<string | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [report, setReport] = useState<string>("")
    const esRef = useRef<EventSource | null>(null)
    const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

    const reset = useCallback(() => {
        esRef.current?.close()
        esRef.current = null
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = null
        setSteps([])
        setStatus("idle")
        setRunId(null)
        setError(null)
        setReport("")
    }, [])

    const startRunFn = useCallback(async (task: string) => {
        reset()
        setStatus("running")

        // The stream token embedded in a stream_url expires after 5 minutes
        // (backend/auth.py's create_stream_token) — openStream() is what
        // actually opens the EventSource, factored out so a proactive
        // refresh (below) can swap in a freshly-minted URL and reopen
        // without duplicating the onmessage/onerror wiring.
        const openStream = (run_id: string, url: string) => {
            const es = new EventSource(resolveStreamUrl(url))
            esRef.current = es

            // Refresh ~30s before the 5-minute token actually expires —
            // proactive, not reactive: EventSource's onerror doesn't expose
            // the HTTP status code a browser received, so there's no
            // reliable way to detect "the token expired" versus "a normal
            // network blip" from onerror alone. A run that's already
            // finished by then just makes this a harmless no-op (the
            // refresh endpoint 404s if the run doesn't exist under this
            // project, but by that point status is already "complete").
            if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
            refreshTimerRef.current = setTimeout(async () => {
                try {
                    const { stream_url } = await refreshStreamToken(run_id)
                    es.close()
                    openStream(run_id, stream_url)
                } catch {
                    // Run likely already completed — nothing to reconnect.
                }
            }, 4.5 * 60 * 1000)

            es.onmessage = (e) => {
                // Guard against empty/malformed data
                if (!e.data || e.data === "[DONE]") {
                    setStatus("complete")
                    es.close()
                    return
                }

                let parsed: SSEEvent
                try {
                    parsed = JSON.parse(e.data)
                } catch {
                    return  // skip unparseable frames
                }

                const evtType = parsed.type

                // Control events
                if (evtType === "run_started") return
                if (evtType === "run_complete") {
                    if (parsed.report) setReport(parsed.report)
                    setStatus("complete")
                    es.close()
                    return
                }
                if (evtType === "error") {
                    setError(parsed.message ?? "Unknown error")
                    setStatus("error")
                    es.close()
                    return
                }

                // Step events — only add if they have agentId + txHash
                // This guards against any unexpected event shapes
                if (isStepEvent(parsed)) {
                    setSteps(prev => [...prev, parsed])
                }
            }

            es.onerror = () => {
                // EventSource auto-reconnects on transient errors
                // Only treat as fatal if status isn't already complete
                setStatus(prev => {
                    if (prev === "running") {
                        setError("Stream connection lost")
                        return "error"
                    }
                    return prev
                })
                es.close()
            }
        }

        try {
            // 1. POST /run-agent → get run_id + a signed, run-scoped stream_url
            const { run_id, stream_url } = await startRun(task)
            setRunId(run_id)

            // 2. Open SSE stream using ONLY the backend-returned URL — there
            // is no other way to construct a valid one (see resolveStreamUrl's
            // own comment in lib/api.ts).
            openStream(run_id, stream_url)
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : "Failed to start run"
            setError(msg)
            setStatus("error")
        }
    }, [reset])

    return {
        steps,
        status,
        runId,
        error,
        report,
        startRun: startRunFn,
        reset,
    }
}