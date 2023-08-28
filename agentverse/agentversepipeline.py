import asyncio
import os

from typing import List, Tuple
import logging

# from agentverse.agents import Agent
from agentverse.logging import get_logger
from agentverse.agents.conversation_agent import BaseAgent

# from agentverse.environments import BaseEnvironment
from agentverse.environments import PipelineEnvironment
from agentverse.initialization import load_agent, load_environment, prepare_task_config
from agentverse.message import Message
from agentverse.utils import AgentCriticism

logger = get_logger(__name__)

openai_logger = logging.getLogger("openai")
openai_logger.setLevel(logging.WARNING)


class AgentVersePipeline:
    agent: List[BaseAgent]
    environment: PipelineEnvironment
    logs: list = []

    def __init__(self, agents: List[BaseAgent], environment: PipelineEnvironment):
        self.agents = agents
        self.environment = environment

    @classmethod
    def from_task(cls, task: str):
        """Build an AgentVerse from a task name.
        The task name should correspond to a directory in `tasks` directory.
        Then this method will load the configuration from the yaml file in that directory.
        """
        # Prepare the config of the task
        task_config = prepare_task_config(task)

        # Build the environment
        env_config = task_config["environment"]
        env_config["role_assigner"] = load_agent(task_config["agents"][0])
        env_config["solver"] = load_agent(task_config["agents"][1])
        env_config["critics"] = [
            load_agent(task_config["agents"][2])
            for i in range(task_config["cnt_critic_agents"])
        ]
        env_config["executor"] = load_agent(task_config["agents"][3])
        env_config["evaluator"] = load_agent(task_config["agents"][4])
        env_config["cnt_critic_agents"] = task_config.get("cnt_critic_agents", 0)
        env_config["task_description"] = task_config.get("task_description", "")
        env_config["max_loop_rounds"] = task_config.get("max_loop_rounds", 3)
        env_config["max_criticizing_rounds"] = task_config.get(
            "max_criticizing_rounds", 3
        )
        env_config["human_eval"] = task_config.get("human_eval", False)
        env_config["task"] = task

        environment: PipelineEnvironment = load_environment(env_config)
        agents = (
            [environment.role_assigner, environment.solver]
            + environment.critics
            + [environment.evaluator]
        )

        return cls(agents, environment)

    def run(
        self,
        single_agent: bool = False,
        discussion_mode: bool = False,
    ):
        """Run the environment from scratch until it is done.

        **Rewrite in pipeline to let the pipeline architecture more clear**
        and more concise (in this file, you can change the workflow
        easily, instead of write distributed code in the environment rules)

        """
        self.environment.reset()
        self.logs = []
        advice = "No advice yet."
        result = ""
        preliminary_solution = "No solution yet."

        while self.environment.cnt_round < self.environment.max_loop_rounds:
            logger.info(f"Loop Round {self.environment.cnt_round}")
            if not discussion_mode and not single_agent:
                # criticizing, multi-agent mode need pre-solution
                preliminary_solution = self.environment.solve(
                    former_solution=preliminary_solution,
                    critic_opinions=[(self.environment.evaluator, advice)],
                )
                self.logs.append({"agent": "solver", "content": preliminary_solution})
                logger.info(f"New Solution:\n{preliminary_solution}")
            if not single_agent:
                preliminary_solution = self.multiagent_criticizing(
                    preliminary_solution, advice, discussion_mode
                )
            else:
                # single agent
                # to let LLM think the same times as multi-agent criticizing
                # like chain of thought
                for i in range(self.environment.max_criticizing_rounds):
                    solutions_in_this_round = []

                    new_step_solution = self.singleagent_thinking(
                        preliminary_solution, advice
                    )
                    solutions_in_this_round.append(new_step_solution)
                    logger.info(f"New Step:\n{new_step_solution}")

                    preliminary_solution += "\n" + "\n".join(solutions_in_this_round)
            # executor execute the final solution, empty now
            logger.info("Execution begins!")
            result = self.environment.execute(preliminary_solution)

            # evaluator evaluate the result
            logger.info("Evaluation begins!")
            score, advice = self.environment.evaluate(result)

            self.logs.append(
                {
                    "agent": "evaluator",
                    "content": f"Evaluation result: Score: {score}\nAdvice: {advice}",
                }
            )
            logger.info(f"Evaluation result: Score: {score}\nAdvice: {advice}")

            # if score too low, then reject
            if score is not None and (
                (isinstance(score, bool) and score is True)
                or (isinstance(score, (list, tuple)) and all([s >= 8 for s in score]))
            ):
                # TODO: 8 is an arbitrary threshold
                self.logs.append({"agent": "system", "content": "Good score! Accept!"})
                logger.info("Good score! Accept!")
                logger.info("Final Result:\n" + result)
                break
            else:
                self.logs.append({"agent": "system", "content": "Bad score! Reject!"})
                logger.info("Bad score! Reject!")
            self.environment.cnt_round += 1
        logger.info("End of whole process!Saving...")
        self.save_result(result, single_agent)
        return result

    def singleagent_thinking(self, preliminary_solution, advice) -> str:
        preliminary_solution = self.environment.solve(
            former_solution=preliminary_solution,
            critic_opinions=[(self.environment.evaluator, advice)],
        )
        return preliminary_solution

    def multiagent_criticizing(
        self,
        preliminary_solution: str = "No solution yet.",
        advice: str = "No advice yet.",
        discussion_mode: bool = False,
    ) -> Tuple[str, List[AgentCriticism]]:
        """The multi-agent criticizing process
        include the solve process after criticizing"""

        roles = self.environment.role_assign(advice)
        self.logs.append({"agent": "role assigner", "content": roles})
        logger.info(f"Roles:\n" + "\n".join(roles))

        for i in range(self.environment.max_criticizing_rounds):
            # critics criticize the solution of solver agent
            criticisms = asyncio.run(
                self.environment.criticize(
                    preliminary_solution, advice if i == 0 else ""
                )
            )
            printed_message = "\n".join(
                [
                    f"({x.sender_agent.role_description}):\n{x.criticism}"
                    for x in criticisms
                ]
            )
            self.logs.append({"agent": "critics", "content": printed_message})
            logger.info(f"Critic Opinions:\n{printed_message}")
            if self.is_consensus_reached(criticisms):
                self.logs.append({"agent": "system", "content": "Consensus reached"})
                logger.info("Consensus reached!")
                break
            criticism = [
                (criticism.sender_agent, criticism.criticism)
                for criticism in criticisms
                if not criticism.is_agree
            ]
            if discussion_mode:
                preliminary_solution = self.environment.summarize(
                    former_solution=preliminary_solution, critic_opinions=criticism
                )
            else:
                preliminary_solution = self.environment.solve(
                    former_solution=preliminary_solution,
                    critic_opinions=criticism,
                )
            self.logs.append({"agent": "solver", "content": preliminary_solution})
            logger.info(
                f"New Solution at round{i} of criticizing:\n{preliminary_solution}"
            )

        final_solution = preliminary_solution
        return final_solution

    def reset(self):
        self.environment.reset()
        for agent in self.agents:
            agent.reset()

    def is_consensus_reached(self, criticisms: List[AgentCriticism]) -> bool:
        """Check if the criticism opinions reach consensus"""
        agree_cnt = 0
        for criticism in criticisms:
            if criticism.is_agree:
                agree_cnt += 1
        logger.info(
            f"{agree_cnt} of {self.environment.cnt_critic_agents} critics agree"
        )
        return agree_cnt == self.environment.cnt_critic_agents
        # TODO: the consensus reaching condition is to be discussed

    def save_result(self, result: str, single_agent: bool = False):
        """Save the result to the result file"""
        if single_agent:
            result_file_path = "../results/" + self.environment.task + "_single.txt"
        else:
            result_file_path = "../results/" + self.environment.task + ".txt"
        os.makedirs(os.path.dirname(result_file_path), exist_ok=True)
        with open(result_file_path, "w") as f:
            f.write(result)
