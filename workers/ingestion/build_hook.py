"""Bundle the canonical catalog into wheels and source distributions."""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        project = Path(self.root)
        for folder, name in (("packages/domain", "topics.json"), ("packages/domain", "agencies.json"), ("config", "meeting-durations.json")):
            canonical = project.parents[1] / folder / name
            packaged = project / "tahoe_ingest" / name
            if canonical.is_file():
                build_data["force_include"][str(canonical)] = f"tahoe_ingest/{name}"
            elif not packaged.is_file():
                raise FileNotFoundError(f"canonical {name} missing from build context")
