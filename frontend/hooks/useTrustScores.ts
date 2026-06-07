"use client"

import { useState, useEffect, useRef } from "react"
import { getTrustScores } from "@/lib/api"
import { TrustScore } from "@/lib/types"

export function useTrustScores(runId: string | null, status: string) {
    const [scores, setScores] = useState<TrustScore[]>([])
    const intervalRef = useRef<NodeJS.Timeout | null>(null)

    useEffect(() => {
        if (intervalRef.current) clearInterval(intervalRef.current)

        if (!runId) {
            setScores([])
            return
        }

        const fetchScores = async () => {
            try {
                const data = await getTrustScores(runId)
                if (data.scores?.length > 0) setScores(data.scores)
            } catch {
                // silent
            }
        }

        // Fetch immediately whenever runId or status changes
        fetchScores()

        // Poll every 3s while running
        if (status === "running") {
            intervalRef.current = setInterval(fetchScores, 3000)
        }

        // Fetch one final time when complete to get final scores
        if (status === "complete") {
            // Small delay to ensure scorer txs are confirmed on-chain
            setTimeout(fetchScores, 2000)
            setTimeout(fetchScores, 5000)  // second attempt in case first is too early
        }

        return () => {
            if (intervalRef.current) clearInterval(intervalRef.current)
        }
    }, [runId, status])

    return { scores }
}