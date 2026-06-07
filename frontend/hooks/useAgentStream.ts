// hooks/useAgentStream.ts
// Manages the SSE connection and parses events into the correct shape

"use client"

import { useState, useCallback, useRef } from "react"
import { startRun, streamUrl } from "@/lib/api"
import { SSEEvent, isStepEvent } from "@/lib/types"

type Status = "idle" | "running" | "complete" | "error"

export function useAgentStream() {
    const [steps, setSteps] = useState<SSEEvent[]>([])
    const [status, setStatus] = useState<Status>("idle")
    const [runId, setRunId] = useState<string | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [report, setReport] = useState<string>("")
    const esRef = useRef<EventSource | null>(null)

    const reset = useCallback(() => {
        esRef.current?.close()
        esRef.current = null
        setSteps([])
        setStatus("idle")
        setRunId(null)
        setError(null)
        setReport("")
    }, [])

    const startRunFn = useCallback(async (task: string) => {
        reset()
        setStatus("running")

        try {
            // 1. POST /run-agent → get run_id
            const { run_id } = await startRun(task)
            setRunId(run_id)

            // 2. Open SSE stream
            const url = streamUrl(run_id)
            const es = new EventSource(url)
            esRef.current = es

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