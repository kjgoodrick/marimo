# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import dataclasses
import sys
import textwrap
from tempfile import TemporaryDirectory
from typing import Any, Generator

import pytest

from marimo._ast.app import CellManager
from marimo._ast.cell import CellId_t
from marimo._config.config import DEFAULT_CONFIG
from marimo._messaging.streams import (
    ThreadSafeStderr,
    ThreadSafeStdin,
    ThreadSafeStdout,
    ThreadSafeStream,
)
from marimo._runtime.context import initialize_context, teardown_context
from marimo._runtime.marimo_pdb import MarimoPdb
from marimo._runtime.requests import AppMetadata, ExecutionRequest
from marimo._runtime.runtime import Kernel


@dataclasses.dataclass
class _MockStream(ThreadSafeStream):
    """Captures the ops sent through the stream"""

    messages: list[tuple[str, dict[Any, Any]]] = dataclasses.field(
        default_factory=list
    )

    def write(self, op: str, data: dict[Any, Any]) -> None:
        self.messages.append((op, data))


class MockStdout(ThreadSafeStdout):
    """Captures the output sent through the stream"""

    def __init__(self, stream: _MockStream) -> None:
        super().__init__(stream)
        self.messages: list[str] = []

    def write(self, data: str) -> int:
        self.messages.append(data)
        return len(data)


class MockStderr(ThreadSafeStderr):
    """Captures the output sent through the stream"""

    messages: list[str] = dataclasses.field(default_factory=list)

    def __init__(self, stream: _MockStream) -> None:
        super().__init__(stream)
        self.messages: list[str] = []

    def write(self, data: str) -> int:
        self.messages.append(data)
        return len(data)


class MockStdin(ThreadSafeStdin):
    """Echoes the prompt."""

    def __init__(self, stream: _MockStream) -> None:
        super().__init__(stream)
        self.messages: list[str] = []

    def _readline_with_prompt(self, prompt: str = "") -> str:
        return prompt


@dataclasses.dataclass
class MockedKernel:
    """Should only be created in fixtures b/c inits a runtime context"""

    stream: _MockStream = dataclasses.field(default_factory=_MockStream)

    def __post_init__(self) -> None:
        self.stdout = MockStdout(self.stream)
        self.stderr = MockStderr(self.stream)
        self.stdin = MockStdin(self.stream)
        self._main = sys.modules["__main__"]

        self.k = Kernel(
            stream=self.stream,
            stdout=self.stdout,
            stderr=self.stderr,
            stdin=self.stdin,
            cell_configs={},
            user_config=DEFAULT_CONFIG,
            app_metadata=AppMetadata(
                query_params={}, filename=None, cli_args={}
            ),
            debugger_override=MarimoPdb(stdout=self.stdout, stdin=self.stdin),
            execute_stale_cells_callback=lambda: None,
        )

        initialize_context(
            kernel=self.k,
            stream=self.stream,  # type: ignore
        )

    def teardown(self) -> None:
        # must be called by fixtures that instantiate this
        teardown_context()
        self.stdout._watcher.stop()
        self.stderr._watcher.stop()
        sys.modules["__main__"] = self._main


# fixture that provides a kernel (and tears it down)
@pytest.fixture
def k() -> Generator[Kernel, None, None]:
    mocked = MockedKernel()
    yield mocked.k
    mocked.teardown()


# fixture that wraps a kernel and other mocked objects
@pytest.fixture
def mocked_kernel() -> Generator[MockedKernel, None, None]:
    mocked = MockedKernel()
    yield mocked
    mocked.teardown()


# Installs an execution context without stream redirection
@pytest.fixture
def executing_kernel() -> Generator[Kernel, None, None]:
    mocked = MockedKernel()
    mocked.k.stdout = None
    mocked.k.stderr = None
    mocked.k.stdin = None
    with mocked.k._install_execution_context(cell_id="0"):
        yield mocked.k
    mocked.teardown()


@pytest.fixture
def temp_marimo_file() -> Generator[str, None, None]:
    tmp_dir = TemporaryDirectory()
    tmp_file = tmp_dir.name + "/notebook.py"
    content = """
    import marimo
    app = marimo.App()

    @app.cell
    def __():
        import marimo as mo
        return mo,

    if __name__ == "__main__":
        app.run()
    """

    with open(tmp_file, "w") as f:
        f.write(content)
        yield tmp_file

    tmp_dir.cleanup()


# Factory to create ExecutionRequests and abstract away cell ID
class ExecReqProvider:
    def __init__(self) -> None:
        self.cell_manager = CellManager()

    def get(self, code: str) -> ExecutionRequest:
        key = self.cell_manager.create_cell_id()
        return ExecutionRequest(cell_id=key, code=textwrap.dedent(code))

    def get_with_id(self, cell_id: CellId_t, code: str) -> ExecutionRequest:
        return ExecutionRequest(cell_id=cell_id, code=textwrap.dedent(code))


# fixture that provides an ExecReqProvider
@pytest.fixture
def exec_req() -> ExecReqProvider:
    return ExecReqProvider()
