import asyncio

from langchain_core.messages import AIMessage

from metricguard.agent import loop
from metricguard.agent.runs import AgentRunStore, RunStatus


class _FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return next(self.responses)


def test_agent_retries_empty_provider_final_and_records_answer(monkeypatch, tmp_path):
    async def no_tools():
        return []

    store = AgentRunStore(directory=tmp_path)
    model = _FakeModel([
        AIMessage(content=""),
        AIMessage(content="The conflict is proven; no new proposals were staged."),
    ])
    monkeypatch.setattr(loop, "build_all_tools", no_tools)
    monkeypatch.setattr(loop, "get_llm", lambda: model)
    monkeypatch.setattr(loop, "AgentRunStore", lambda: store)

    result = asyncio.run(loop.arun_agent_result("Audit revenue", verbose=False))
    assert result.answer.startswith("The conflict is proven")
    recorded = store.get(result.run_id)
    assert recorded.status == RunStatus.COMPLETED
    assert recorded.final_answer == result.answer


def test_agent_rejects_unsupported_final_claims(monkeypatch, tmp_path):
    async def no_tools():
        return []

    store = AgentRunStore(directory=tmp_path)
    model = _FakeModel([
        AIMessage(content="The model is version-controlled and peer-reviewed."),
        AIMessage(content="The DataHub evidence supports the recommendation."),
    ])
    monkeypatch.setattr(loop, "build_all_tools", no_tools)
    monkeypatch.setattr(loop, "get_llm", lambda: model)
    monkeypatch.setattr(loop, "AgentRunStore", lambda: store)

    result = asyncio.run(loop.arun_agent_result("Audit revenue", verbose=False))
    assert result.answer == "The DataHub evidence supports the recommendation."
