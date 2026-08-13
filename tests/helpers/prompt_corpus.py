"""Typed policies for the shared agent prompt fixture corpus."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Mapping
from typing import TypedDict

import yaml


class PromptCorpusPreset(Enum):
    """The intentional format, path, and empty-document policies of each consumer."""

    AGENT_TUI = "agent-tui"
    AUTO_APPROVE = "auto-approve"
    MOCK_AGENTS = "mock-agents"


class PromptCorpusCase(TypedDict):
    inventory: dict[str, Any]
    data: Any
    path: Path
    text: str


@dataclass(frozen=True)
class PromptCorpus:
    root: Path
    preset: PromptCorpusPreset

    def load(self, path: Path) -> Any:
        text = path.read_text(encoding="utf-8")
        if self.preset is not PromptCorpusPreset.MOCK_AGENTS and path.suffix == ".json":
            return json.loads(text)
        data = yaml.safe_load(text)
        if self.preset is PromptCorpusPreset.MOCK_AGENTS and data is None:
            return {}
        return data

    def visible_text(self, path: Path) -> str:
        if self.preset is PromptCorpusPreset.AUTO_APPROVE and path.suffix not in {".json", ".yaml", ".yml"}:
            return path.read_text(encoding="utf-8")
        data = self.load(path)
        if not isinstance(data, Mapping):
            return ""
        return str(data.get("raw_capture") or data.get("visible_text") or "")

    def resolve(self, inventory_path: Path, file_name: str) -> Path:
        if self.preset is PromptCorpusPreset.MOCK_AGENTS and inventory_path.parent.name == "captures":
            return inventory_path.parent / file_name
        return self.root / file_name

    def inventory(self, path: Path | None = None) -> Any:
        return self.load(path or self.root / "inventory.yaml")

    def cases(
        self,
        inventory_path: Path | None = None,
        *,
        include: Callable[[Mapping[str, Any], Any], bool] | None = None,
    ) -> list[PromptCorpusCase]:
        source = inventory_path or self.root / "inventory.yaml"
        inventory = self.inventory(source)
        result: list[PromptCorpusCase] = []
        for item in inventory["fixtures"]:
            path = self.resolve(source, str(item["file"]))
            data = self.load(path)
            if include is not None and not include(item, data):
                continue
            result.append({
                "inventory": dict(item),
                "data": data,
                "path": path,
                "text": self.visible_text(path),
            })
        return result
