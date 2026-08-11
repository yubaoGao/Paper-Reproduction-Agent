"""Opt-in real DeepSeek/Qwen semantic-planning smoke test."""
import os,unittest
from backend.app.agents.planner import ReproductionPlannerAgent
from backend.app.domain import PlanningOverrides
from backend.app.llm import DeepSeekStructuredLLMAdapter,LLMPlatformSettings,LLMRouter,QwenStructuredLLMAdapter
from tests.unit.test_reproduction_planner import fixture

ENABLED=os.getenv("REPRODUCTION_PLANNER_INTEGRATION")=="1" and bool(os.getenv("DEEPSEEK_API_KEY")) and bool(os.getenv("DASHSCOPE_API_KEY"))

@unittest.skipUnless(ENABLED,"set REPRODUCTION_PLANNER_INTEGRATION=1 plus DEEPSEEK_API_KEY and DASHSCOPE_API_KEY")
class ReproductionPlannerDeepSeekIntegrationTests(unittest.TestCase):
    def test_real_semantic_selection_is_catalog_bounded(self):
        spec,paper,repo,align=fixture(); second=repo.entrypoints[0].model_copy(update={"entrypoint_id":"ep2","path":"evaluate.py"}); repo=repo.model_copy(update={"entrypoints":(*repo.entrypoints,second)})
        record=align.experiment_alignments[0].model_copy(update={"entrypoint_ids":("ep","ep2")}); align=align.model_copy(update={"experiment_alignments":(record,)})
        settings=LLMPlatformSettings.from_env(); router=LLMRouter(DeepSeekStructuredLLMAdapter(settings.primary),QwenStructuredLLMAdapter(settings.fast))
        result=ReproductionPlannerAgent(router).plan(spec,paper,repo,align,overrides=PlanningOverrides(dataset_bindings={"D":"/d"}))
        self.assertIn(result.plan.experiments[0].resolved_command.entrypoint_id,{"ep","ep2"})
        self.assertGreaterEqual(result.trace.primary_calls,1)

if __name__=="__main__": unittest.main()
