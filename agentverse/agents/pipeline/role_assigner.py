from __future__ import annotations

import asyncio

from agentverse.logging import get_logger
import bdb
from string import Template
from typing import TYPE_CHECKING, List

from agentverse.message import Message

from agentverse.agents import agent_registry
from agentverse.agents.base import BaseAgent
# from agentverse.environments import PipelineEnvironment


logger = get_logger(__name__)


@agent_registry.register("role_assigner")
class RoleAssignerAgent(BaseAgent):
    environment: object = None

    def step(self, advice) -> List[str]:
        prompt = self._fill_prompt_template(advice)
        # logger.info(f"Prompt:\n{prompt}")
        parsed_response = None
        for i in range(self.max_retry):
            try:
                response = self.llm.generate_response(prompt)
                parsed_response = self.output_parser.parse(response)
                break
            except (KeyboardInterrupt, bdb.BdbQuit):
                raise
            except Exception as e:
                logger.error(e)
                logger.warning("Retrying...")
                continue

        if parsed_response is None:
            logger.error(f"{self.name} failed to generate valid response.")

        return parsed_response

    async def astep(self, env_description: str = "") -> Message:
        """Asynchronous version of step"""
        pass

    def _fill_prompt_template(self, advice) -> str:
        """Fill the placeholders in the prompt template

        In the role_assigner agent, three placeholders are supported:
        - ${task_description}
        - ${cnt_critic_agnets}
        - ${advice}
        """
        input_arguments = {
            "task_description": self.environment.task_description,
            "cnt_critic_agents": self.environment.cnt_critic_agents,
            "advice": advice,
        }
        return Template(self.prompt_template).safe_substitute(input_arguments)

    def add_message_to_memory(self, messages: List[Message]) -> None:
        self.memory.add_message(messages)

    def reset(self) -> None:
        """Reset the agent"""
        self.memory.reset()
        # TODO: reset receiver
