from __future__ import annotations

import os
from typing import TypedDict

import httpx

from langgraph.graph import END, START, StateGraph


class ControlState(TypedDict, total=False):
    input: str
    result: str
    node: str
    worker: str


def controller_node(state: ControlState) -> ControlState:
    text = state.get("input", "")
    worker_url = first_worker_url()
    if worker_url:
        try:
            response = httpx.post(
                f"{worker_url}/run",
                json={"tool": "echo", "input": text},
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
            return {
                "input": text,
                "result": str(payload.get("result", "")),
                "node": "controller",
                "worker": worker_url,
            }
        except Exception as exc:
            return {
                "input": text,
                "result": f"worker call failed: {exc.__class__.__name__}",
                "node": "controller",
                "worker": worker_url,
            }

    return {
        "input": text,
        "result": f"langgraph-control received: {text}",
        "node": "controller",
    }


def first_worker_url() -> str | None:
    raw_urls = os.getenv("WORKER_URLS", "")
    urls = [url.rstrip("/") for url in raw_urls.split(",") if url.strip()]
    return urls[0] if urls else None


def build_graph():
    graph = StateGraph(ControlState)
    graph.add_node("controller", controller_node)
    graph.add_edge(START, "controller")
    graph.add_edge("controller", END)
    return graph.compile()


compiled_graph = build_graph()
