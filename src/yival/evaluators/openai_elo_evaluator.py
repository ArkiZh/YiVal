"""
Elo Evaluators Module.

This module contains the OpenAIEloEvaluator class, which implements an
ELO-based evaluation system. The ELO system is used to rank different model
outputs based on human evaluations, and this specific 
implementation interfaces with the OpenAI API for those evaluations.

"""
import asyncio
import itertools
import re
from math import comb
from typing import Dict, List, Tuple

import openai
from tqdm import tqdm

from ..common.utils import parallel_completions
from ..schemas.evaluator_config import EvaluatorType, OpenAIEloEvaluatorConfig
from ..schemas.experiment_config import (
    CombinationAggregatedMetrics,
    EvaluatorOutput,
    Experiment,
    ExperimentResult,
    GroupedExperimentResult,
    InputData,
    Metric,
)
from .base_evaluator import BaseEvaluator

K = 32  # Elo rating constant
RANKING_SYSTEM_PROMPT = """Your job is to rank the quality of two outputs
generated by different prompts. The prompts are used to generate a response
for a given task and its associated input data.
You will be provided with the task description, the test input data, and two
generations - one for each system prompt.
Rank the generations in order of quality. If Generation A is better, respond
with 'A'. If Generation B is better, respond with 'B'.
Remember, to be considered 'better', a generation must not just be good, it
must be noticeably superior to the other. Also, keep in mind that you are a
very harsh critic. Only rank a generation as better if it truly impresses you
more than the other. Respond with your ranking, and nothing else. Be fair and
unbiased in your judgement."""


class OpenAIEloEvaluator(BaseEvaluator):
    """
    OpenAIEloEvaluator is an evaluator that uses the ELO rating system to rank
    model outputs.
    """
    config: OpenAIEloEvaluatorConfig
    default_config: OpenAIEloEvaluatorConfig = OpenAIEloEvaluatorConfig(
        name="openai_elo_evaluator", evaluator_type=EvaluatorType.ALL
    )

    def __init__(self, config: OpenAIEloEvaluatorConfig):
        super().__init__(config)
        self.config: OpenAIEloEvaluatorConfig = config

    def expected_score(self, r1, r2):
        """
        Calculate the expected score between two ratings.
        """
        return 1 / (1 + 10**((r2 - r1) / 400))

    def update_elo(self, r1, r2, score1) -> Tuple[float, float]:
        e1 = self.expected_score(r1, r2)
        e2 = self.expected_score(r2, r1)
        return r1 + K * (score1 - e1), r2 + K * ((1 - score1) - e2)

    def get_score(
        self, test_case, result1: ExperimentResult, result2: ExperimentResult
    ) -> float:
        score = openai.ChatCompletion.create(
            model=self.config.openai_model_name,
            messages=[{
                "role": "system",
                "content": RANKING_SYSTEM_PROMPT
            }, {
                "role":
                "user",
                "content":
                f"""Task: {self.config.input_description.strip()}
    Prompt: {test_case}
    Generation A: {result1.raw_output}
    Generation B: {result2.raw_output}"""
            }],
            logit_bias={
                '32': 100,  # 'A' token
                '33': 100,  # 'B' token
            },
            max_tokens=1,
            temperature=0.5,
        ).choices[0].message.content
        return score

    def evaluate_based_on_all_results(
        self, experiment: List[Experiment]
    ) -> None:
        if len(experiment) != 1:
            return

        prompt_ratings: Dict[str, float] = {
            combo.combo_key: 1200
            for combo in experiment[0].combination_aggregated_metrics
        }
        total_rounds = sum(
            comb(len(group_experiment_result.experiment_results), 2) for
            group_experiment_result in experiment[0].group_experiment_results
        ) * 2

        pbar = tqdm(total=total_rounds, ncols=70)
        message_batches = []

        for group_experiment_result in experiment[0].group_experiment_results:
            test_case = group_experiment_result.group_key
            pattern = r'content:\s*(\{.*?\})(?:,|$)'
            match = re.search(pattern, test_case)

            if match:
                test_case = match.group(1)
            else:
                test_case = ""

            for result1, result2 in itertools.combinations(
                group_experiment_result.experiment_results, 2
            ):
                message1 = [{
                    "role": "system",
                    "content": RANKING_SYSTEM_PROMPT
                }, {
                    "role":
                    "user",
                    "content":
                    f"""Task: {self.config.input_description.strip()}
                        Prompt: {test_case}
                        Generation A: {result1.raw_output}
                        Generation B: {result2.raw_output}"""
                }]
                message_batches.append(message1)

                message2 = [{
                    "role": "system",
                    "content": RANKING_SYSTEM_PROMPT
                }, {
                    "role":
                    "user",
                    "content":
                    f"""Task: {self.config.input_description.strip()}
                        Prompt: {test_case}
                        Generation A: {result2.raw_output}
                        Generation B: {result1.raw_output}"""
                }]
                message_batches.append(message2)
        # 2. Utilizing parallel_completions:
        with tqdm(
            total=total_rounds, desc="Generating Scores", unit="score"
        ) as pbar:
            responses = asyncio.run(
                parallel_completions(
                    message_batches,
                    self.config.openai_model_name,
                    max_tokens=1,
                    temperature=0.5,
                    presence_penalty=0,
                    pbar=pbar
                )
            )

            idx = 0
            for group_experiment_result in experiment[
                0].group_experiment_results:
                for result1, result2 in itertools.combinations(
                    group_experiment_result.experiment_results, 2
                ):
                    pbar.update()
                    formatted_combination1 = str(result1.combination)
                    formatted_combination2 = str(result2.combination)
                    score1 = responses[idx]["choices"][0]["message"]["content"]
                    score1 = 1 if score1 == 'A' else 0 if score1 == 'B' else 0.5
                    idx += 1
                    score2 = responses[idx]["choices"][0]["message"]["content"]
                    score2 = 1 if score2 == 'A' else 0 if score2 == 'B' else 0.5
                    idx += 1
                    r1, r2 = prompt_ratings[formatted_combination1
                                            ], prompt_ratings[
                                                formatted_combination2]
                    r1, r2 = self.update_elo(r1, r2, score1)
                    prompt_ratings[formatted_combination1], prompt_ratings[
                        formatted_combination2] = r1, r2

            pbar.close()
        for index, combo in enumerate(
            experiment[0].combination_aggregated_metrics
        ):
            if not combo.evaluator_outputs:
                experiment[0].combination_aggregated_metrics[
                    index].evaluator_outputs = []
            experiment[0].combination_aggregated_metrics[index].evaluator_outputs.append( # type: ignore
                EvaluatorOutput(
                    name="openai_elo_evaluator",
                    result=prompt_ratings[combo.combo_key]
                )
            )


BaseEvaluator.register_evaluator(
    "openai_elo_evaluator", OpenAIEloEvaluator, OpenAIEloEvaluatorConfig
)


def create_test_data_v2():
    # Mock InputData
    input_data1 = InputData(content={"text": "Hello world!"})
    input_data2 = InputData(content={"text": "How are you?"})

    # Mock ExperimentResults for Test Case 1
    er1 = ExperimentResult(
        input_data=input_data1,
        combination={"name": "A"},
        raw_output="Bonjour le monde!",
        latency=100,
        token_usage=5
    )
    er2 = ExperimentResult(
        input_data=input_data2,
        combination={"name": "A"},
        raw_output="Comment ça va?",
        latency=100,
        token_usage=5
    )
    er3 = ExperimentResult(
        input_data=input_data1,
        combination={"name": "B"},
        raw_output="Salut monde!",
        latency=150,
        token_usage=6
    )
    er4 = ExperimentResult(
        input_data=input_data2,
        combination={"name": "B"},
        raw_output="Comment tu es?",
        latency=150,
        token_usage=6
    )
    er5 = ExperimentResult(
        input_data=input_data1,
        combination={"name": "C"},
        raw_output="Bonjour monde!",
        latency=130,
        token_usage=6
    )
    er6 = ExperimentResult(
        input_data=input_data2,
        combination={"name": "C"},
        raw_output="Comment vas-tu?",
        latency=130,
        token_usage=5
    )

    # Grouped Experiment Results using str(item.input_data) for group_key
    ger1 = GroupedExperimentResult(
        group_key=str(input_data1.content), experiment_results=[er1, er3, er5]
    )
    ger2 = GroupedExperimentResult(
        group_key=str(input_data2.content), experiment_results=[er2, er4, er6]
    )

    # Combination Aggregated Metrics
    cam1 = CombinationAggregatedMetrics(
        combo_key=str(er1.combination),
        experiment_results=[er1, er2],
        aggregated_metrics={"accuracy": [Metric("accuracy", 0.95)]}
    )
    cam2 = CombinationAggregatedMetrics(
        combo_key=str(er3.combination),
        experiment_results=[er3, er4],
        aggregated_metrics={"accuracy": [Metric("accuracy", 0.85)]}
    )
    cam3 = CombinationAggregatedMetrics(
        combo_key=str(er5.combination),
        experiment_results=[er5, er6],
        aggregated_metrics={"accuracy": [Metric("accuracy", 0.90)]}
    )

    # Mock Experiment for Test Case 1
    experiment1 = Experiment(
        group_experiment_results=[ger1, ger2],
        combination_aggregated_metrics=[cam1, cam2, cam3]
    )

    return experiment1


def main():
    evaluator = OpenAIEloEvaluator(
        OpenAIEloEvaluatorConfig(
            name="openai_elo_evaluator",
            input_description="Translate the given English sentence to French",
            evaluator_type=EvaluatorType.ALL,
        )
    )
    experiment = create_test_data_v2()
    evaluator.evaluate_based_on_all_results([experiment])
    print(experiment)


if __name__ == "__main__":
    main()
