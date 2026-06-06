"""
web_search MCP server — port 8001
Exposes two tools to LangGraph agents via FastMCP:
  - search_web(query, max_results)
  - fact_check(claim, context)
"""

import os
import logging
from typing import Optional

from fastmcp import FastMCP
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY not set in .env")

tavily = TavilyClient(api_key=TAVILY_API_KEY)

mcp = FastMCP("web_search")


@mcp.tool()
def search_web(query: str, max_results: int = 5) -> dict:
    """
    Search the web using Tavily and return structured results.

    Args:
        query:       The search query string.
        max_results: Number of results to return (default 5, max 10).

    Returns:
        dict with keys:
          - query:   the original query
          - results: list of {title, url, content, score}
          - answer:  Tavily's auto-generated answer summary (may be empty)
    """
    max_results = min(max_results, 10)
    logger.info("[search_web] query=%r max_results=%d", query, max_results)

    try:
        response = tavily.search(
            query=query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=True,
        )

        results = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", ""),
                "score":   r.get("score", 0.0),
            }
            for r in response.get("results", [])
        ]

        return {
            "query":   query,
            "results": results,
            "answer":  response.get("answer", ""),
        }

    except Exception as e:
        logger.error("[search_web] error: %s", e)
        return {"query": query, "results": [], "answer": "", "error": str(e)}


@mcp.tool()
def fact_check(claim: str, context: Optional[str] = None) -> dict:
    """
    Fact-check a claim by searching for corroborating or contradicting evidence.

    Args:
        claim:   The statement to verify.
        context: Optional additional context to narrow the search.

    Returns:
        dict with keys:
          - claim:      the original claim
          - verdict:    'supported' | 'contradicted' | 'unverified'
          - confidence: float 0.0-1.0
          - evidence:   list of {title, url, content, score}
          - summary:    short explanation of the verdict
    """
    search_query = f"fact check: {claim}"
    if context:
        search_query += f" {context}"

    logger.info("[fact_check] claim=%r", claim)

    try:
        response = tavily.search(
            query=search_query,
            max_results=5,
            search_depth="advanced",
            include_answer=True,
        )

        results = response.get("results", [])
        answer  = response.get("answer", "")

        # Simple heuristic verdict based on Tavily answer content
        claim_lower  = claim.lower()
        answer_lower = answer.lower()

        contradiction_signals = ["false", "incorrect", "misleading", "not true", "debunked", "myth"]
        support_signals       = ["true", "correct", "confirmed", "accurate", "verified"]

        verdict    = "unverified"
        confidence = 0.5

        if any(s in answer_lower for s in contradiction_signals):
            verdict    = "contradicted"
            confidence = 0.75
        elif any(s in answer_lower for s in support_signals):
            verdict    = "supported"
            confidence = 0.75
        elif answer:
            verdict    = "supported"
            confidence = 0.6

        evidence = [
            {
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "content": r.get("content", ""),
                "score":   r.get("score", 0.0),
            }
            for r in results[:3]
        ]

        return {
            "claim":      claim,
            "verdict":    verdict,
            "confidence": confidence,
            "evidence":   evidence,
            "summary":    answer or "No summary available.",
        }

    except Exception as e:
        logger.error("[fact_check] error: %s", e)
        return {
            "claim":      claim,
            "verdict":    "unverified",
            "confidence": 0.0,
            "evidence":   [],
            "summary":    f"Error during fact check: {e}",
        }


if __name__ == "__main__":
    logger.info("Starting web_search MCP server on port 8001...")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)