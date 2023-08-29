import os
import json
import shutil

# from agentverse.agentverse import AgentVerse
from agentverse.agentversepipeline import AgentVersePipeline
from agentverse.logging import get_logger
from argparse import ArgumentParser
import asyncio
from dataloader import dataloader_registry

logger = get_logger(__file__)

parser = ArgumentParser()

parser.add_argument("--task", type=str, default="responsegen")
parser.add_argument("--dataset_path", type=str, required=True)
parser.add_argument("--output_path", type=str, default=None)
parser.add_argument("--single_agent", "-s", action="store_true")
parser.add_argument("--discussion_mode", "-d", action="store_true")
parser.add_argument("--overwrite", action="store_true")
args = parser.parse_args()


def get_dataloader(task, dataset_path):
    return dataloader_registry.build(task, path=dataset_path)


if __name__ == "__main__":
    dataloader = get_dataloader(args.task, args.dataset_path)
    if args.output_path is None:
        os.makedirs(f"./results/{args.task}", exist_ok=True)
        args.output_path = f"./results/{args.task}"
    else:
        os.makedirs(args.output_path, exist_ok=True)
    shutil.copyfile(
        f"./agentverse/tasks/{args.task}/config.yaml",
        f"{args.output_path}/config.yaml",
    )

    skip_cnt = 0
    if not args.overwrite and os.path.exists(f"{args.output_path}/results.jsonl"):
        with open(f"{args.output_path}/results.jsonl", "r") as f:
            for line in f:
                if line.strip():
                    skip_cnt += 1
    f = open(f"{args.output_path}/results.jsonl", "w" if args.overwrite else "a")
    for i, example in enumerate(dataloader):
        if i < skip_cnt:
            continue
        logger.info(f"Input: {example['input']}\nAnswer: {example['answer']}")
        agentversepipeline = AgentVersePipeline.from_task(args.task)
        agentversepipeline.environment.set_task_description(example["input"])
        response = agentversepipeline.run(
            single_agent=args.single_agent, discussion_mode=args.discussion_mode
        )
        f.write(
            json.dumps(
                {
                    "input": example["input"],
                    "response": response,
                    "label": example["answer"],
                    "logs": agentversepipeline.logs,
                }
            )
            + "\n"
        )
        f.flush()
    f.close()
