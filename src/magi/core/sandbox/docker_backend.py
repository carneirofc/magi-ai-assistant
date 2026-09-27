"""Run each command in a throwaway Docker container.

Hardening: no network (unless `sandbox.docker_network`), read-only root
filesystem with a small tmpfs `/tmp`, all capabilities dropped,
no-new-privileges, the host user's uid:gid (so workspace files stay yours),
memory / pid / CPU limits, and only the user's workspace mounted at
`/workspace`. The container is removed afterwards, even on timeout.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from docker.errors import DockerException
from docker.models.containers import Container
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

import docker
from magi.core.sandbox.backend import ExecResult, cap_output


@dataclass(frozen=True)
class ContainerSpec:
    """The hardened `containers.run` arguments for one command (pure; tested)."""

    image: str
    command: list[str]
    volumes: dict[str, dict[str, str]]
    network_disabled: bool
    user: str
    mem_limit: str
    pids_limit: int
    nano_cpus: int
    working_dir: str = "/workspace"
    read_only: bool = True
    tmpfs: dict[str, str] = field(default_factory=lambda: {"/tmp": "rw,size=64m"})
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    security_opt: list[str] = field(default_factory=lambda: ["no-new-privileges"])
    environment: dict[str, str] = field(default_factory=lambda: {"HOME": "/workspace"})


def container_spec(
    command: str,
    *,
    workspace: Path,
    image: str,
    network: bool,
    memory: str,
    pids: int,
    cpus: float,
) -> ContainerSpec:
    return ContainerSpec(
        image=image,
        command=["bash", "-c", command],
        volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
        network_disabled=not network,
        user=f"{os.getuid()}:{os.getgid()}",
        mem_limit=memory,
        pids_limit=pids,
        nano_cpus=int(cpus * 1e9),
    )


class DockerBackend:
    name = "docker"

    def __init__(
        self,
        *,
        image: str,
        network: bool,
        memory: str,
        pids: int,
        cpus: float,
        output_limit: int,
    ) -> None:
        self.image = image
        self.network = network
        self.memory = memory
        self.pids = pids
        self.cpus = cpus
        self.output_limit = output_limit

    def run(self, command: str, *, workspace: Path, timeout: float) -> ExecResult:
        workspace.mkdir(parents=True, exist_ok=True)
        client = docker.from_env()
        spec = container_spec(
            command,
            workspace=workspace,
            image=self.image,
            network=self.network,
            memory=self.memory,
            pids=self.pids,
            cpus=self.cpus,
        )
        container: Container = client.containers.run(
            image=spec.image,
            command=spec.command,
            working_dir=spec.working_dir,
            volumes=spec.volumes,
            environment=spec.environment,
            network_disabled=spec.network_disabled,
            read_only=spec.read_only,
            tmpfs=spec.tmpfs,
            user=spec.user,
            cap_drop=spec.cap_drop,
            security_opt=spec.security_opt,
            mem_limit=spec.mem_limit,
            pids_limit=spec.pids_limit,
            nano_cpus=spec.nano_cpus,
            detach=True,
        )
        timed_out = False
        try:
            try:
                code = container.wait(timeout=timeout)["StatusCode"]
            except ReadTimeout, RequestsConnectionError:
                container.kill()
                timed_out, code = True, -9
            raw = container.logs(stdout=True, stderr=True)
        finally:
            try:
                container.remove(force=True)
            except DockerException:
                pass
        text, truncated = cap_output(raw.decode("utf-8", errors="replace"), self.output_limit)
        return ExecResult(exit_code=code, output=text, timed_out=timed_out, truncated=truncated)
