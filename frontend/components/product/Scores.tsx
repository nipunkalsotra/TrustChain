"use client";
import { useCallback } from "react";
import { getLeaderboard } from "@/lib/api";
import {
  Empty,
  ErrorBox,
  ExportButton,
  Loading,
  PageTitle,
  Panel,
  useResource,
} from "./ui";
export default function Scores() {
  const resource = useResource(
    useCallback(() => getLeaderboard(200), []),
    30000,
  );
  return (
    <>
      <PageTitle
        title="Trust scores"
        description="Compare observed agent scores across your project’s latest runs."
      >
        <ExportButton data={resource.data} name="trustchain-scores.json" />
      </PageTitle>
      <ErrorBox error={resource.error} retry={resource.reload} />
      <Panel
        title="Agent performance"
        description={`Based on ${resource.data?.runsConsidered ?? 0} runs. Scores reflect pipeline evaluations, not a guarantee of correctness.`}
      >
        {resource.loading ? (
          <Loading />
        ) : resource.data?.agents.length ? (
          <div className="p-table-wrap">
            <table className="p-table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Average score</th>
                  <th>Best score</th>
                  <th>Runs observed</th>
                </tr>
              </thead>
              <tbody>
                {[...resource.data.agents]
                  .sort((a, b) => b.avgScore - a.avgScore)
                  .map((a) => (
                    <tr key={a.agentId}>
                      <td>{a.agentId}</td>
                      <td>
                        <div className="score-meter">
                          <span
                            style={{
                              width: `${Math.max(0, Math.min(100, a.avgScore))}%`,
                            }}
                          />
                        </div>
                        {a.avgScore.toFixed(1)} / 100
                      </td>
                      <td>{a.bestScore} / 100</td>
                      <td>{a.runsCount}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="Scores come with experience"
            description="After your pipeline runs complete and scores are indexed, agent performance will appear here."
          />
        )}
      </Panel>
    </>
  );
}
