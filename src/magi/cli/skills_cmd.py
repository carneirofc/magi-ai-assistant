"""`magi skills [list]` — what the assistant can do: skills, then toolsets."""

from typing import TextIO

from magi.core.config import config


def cmd_list(out: TextIO) -> int:
    from magi.agent.skills import active_skills, all_skills
    from magi.agent.toolsets import TOOLSETS
    from magi.core.skills_fs import skill_dirs

    active = {s.name for s in active_skills()}
    skills = all_skills()
    out.write("Skills" + (":\n" if skills else ": none\n"))
    for skill in skills:
        mark = "✓" if skill.name in active else "·"
        about = skill.description or "(inline prompt)"
        out.write(f"  {mark} {skill.name:<24} {skill.source:<6} {about}\n")
    out.write(f"  searched: {', '.join(str(d) for d in skill_dirs())}\n")
    out.write(f"  agent_write: {config.skills.agent_write}\n\nToolsets:\n")
    disabled = set(config.toolsets.disabled)
    for name, about in TOOLSETS.items():
        mark = "·" if name in disabled else "✓"
        out.write(f"  {mark} {name:<14} {about}\n")
    return 0
