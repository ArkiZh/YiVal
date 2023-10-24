import os

import openai

from yival.logger.token_logger import TokenLogger
from yival.schemas.experiment_config import MultimodalOutput
from yival.states.experiment_state import ExperimentState
from yival.wrappers.string_wrapper import StringWrapper


def write(restaurant: str,
          style: str,
          dishes: str,
          customers: str,
          state: ExperimentState
          ) -> MultimodalOutput:
    logger = TokenLogger()
    logger.reset()
    openai.api_key = os.getenv("OPENAI_API_KEY")

    variables = {
        "restaurant": restaurant,
        "style": style,
        "dishes": dishes,
        "customers": customers}
    prompt = str(StringWrapper("", name="task", state=state, variables=variables))

    messages = [{"role": "user", "content": prompt}]
    print(f"\n=============== Ask llm to write copywriting:\n{prompt}")
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages,
        temperature=1.0,
        max_tokens=2000
    )
    content = response['choices'][0]['message']['content']
    print(f"\n=============== Got the copywriting:\n{content}\n")
    token_usage = response['usage']['total_tokens']
    logger.log(token_usage)

    res = MultimodalOutput(text_output=content)
    return res
