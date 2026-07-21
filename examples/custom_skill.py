"""A complete skill in one manifest — register → boot → the assistant uses it.

A Skill bundles what used to be three separate seams (a prompt overlay file, a
tool registration, a config gate) into one registrable unit. This example
registers a dice-rolling skill and builds the real team, then proves both
halves attached: the prompt fragment is in the lead's instructions and the
tool is on its roster.

Runs offline as-is (building a team never calls the model backend):

    python examples/custom_skill.py

To actually chat with the skill, point the engine at a reachable backend
(e.g. a local llama-server, see examples/desktop_chat.py) and add --chat:

    python examples/custom_skill.py --chat "roll 2d6 for me"
"""

from __future__ import annotations

import argparse
import random
import sys

from agno.tools import tool

from magi.agent.skills import Skill, register_skill
from magi.agent.tools.outputs import ToolOutput, ok


@tool(name="roll_dice", show_result=True)
def roll_dice(count: int = 1, sides: int = 6) -> ToolOutput[list[int]]:
    """Roll `count` dice with `sides` faces each. Call whenever the user asks
    for dice, a random roll, or an NdM expression (e.g. 2d6)."""
    rolls = [random.randint(1, sides) for _ in range(max(1, count))]
    return ok(f"Rolled {count}d{sides}: {rolls} (total {sum(rolls)})", rolls)


register_skill(
    Skill(
        name="dice",
        prompt=(
            "You can roll dice. When the user asks for a roll or randomness, "
            "call `roll_dice` and report the individual rolls and the total — "
            "never invent numbers yourself."
        ),
        tools=(roll_dice,),
    )
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", metavar="MESSAGE", help="send one message through the brain")
    args = parser.parse_args()

    from magi.agent.team import build_team
    from magi.core.memory import build_memory_from_config

    team = build_team(build_memory_from_config())
    tool_names = [getattr(t, "name", type(t).__name__) for t in (team.tools or [])]

    assert "roll_dice" in tool_names, "skill tool did not attach"
    assert "Skill: dice" in team.instructions, "skill prompt did not compose"
    print("skill 'dice' attached: roll_dice on the lead, prompt fragment composed.")

    if args.chat:
        from magi.client import SyncClient, embed

        ui = SyncClient(embed(user_id="example"))
        try:
            print(ui.send(args.chat).text)
        finally:
            ui.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
