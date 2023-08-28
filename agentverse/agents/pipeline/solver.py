from __future__ import annotations

import asyncio

from agentverse.logging import get_logger
import bdb
from string import Template
from typing import TYPE_CHECKING, List, Tuple

# from agentverse.environments import PipelineEnvironment
from agentverse.message import Message

from agentverse.agents import agent_registry
from agentverse.agents.base import BaseAgent


logger = get_logger(__name__)


@agent_registry.register("solver")
class SolverAgent(BaseAgent):
    prompt_template: List[str]

    def step(
        self,
        former_solution: str,
        critic_opinions: List[Tuple[object, str]],
        discussion_mode: bool = False,
        task_description: str = "",
    ) -> Message:
        prompt = self._fill_prompt_template(
            former_solution, critic_opinions, discussion_mode, task_description
        )
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

        message = Message(
            content=""
            if parsed_response is None
            else parsed_response.return_values["output"],
            sender=self.name,
            receiver=self.get_receiver(),
        )
        return message

    async def astep(self, env_description: str = "") -> Message:
        """Asynchronous version of step"""
        pass

    def _fill_prompt_template(
        self,
        former_solution: str,
        critic_opinions: List[Tuple[object, str]],
        discussion_mode: bool,
        task_description: str,
    ) -> str:
        """Fill the placeholders in the prompt template

        In the role_assigner agent, three placeholders are supported:
        - ${task_description}
        - ${former_solution}
        - ${critic_messages}
        """
        input_arguments = {
            "task_description": task_description,
            "former_solution": former_solution,
            "critic_opinions": "\n".join(
                [f"{a.role_description} said: {o}" for a, o in critic_opinions]
            ),
        }
        if discussion_mode:
            template = Template(self.prompt_template[1])
        else:
            template = Template(self.prompt_template[0])
        return template.safe_substitute(input_arguments)

    def add_message_to_memory(self, messages: List[Message]) -> None:
        self.memory.add_message(messages)

    def reset(self) -> None:
        """Reset the agent"""
        self.memory.reset()
        # TODO: reset receiver
