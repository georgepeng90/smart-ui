"""Build the single C# source file consumed by Unity CLI run_script."""

from pathlib import Path


SCRIPT_NAME = "SmartUIBuild.cs"
SCRIPT_ENTRY = "SmartUI.UGUI.Editor.PrefabBuild.Execute"
SCRIPT_DIRECTORY = Path(__file__).resolve().with_name("unity_scripts")
SOURCE_FILES = (
    "BuildContract.cs",
    "BuildFiles.cs",
    "BuildResources.cs",
    "ManagedProperties.cs",
    "PrefabAssets.cs",
    "PrefabBuild.cs",
    "PrefabGraph.cs",
)


def write_script(destination, *, validation_source=None):
    sources = [SCRIPT_DIRECTORY / "src" / name for name in SOURCE_FILES]
    if validation_source is not None:
        sources.append(validation_source)
    imports = set()
    bodies = []
    for source in sources:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
        imports.update(line for line in lines if line.startswith("using "))
        bodies.append("\n".join(line for line in lines if not line.startswith("using ")).strip())
    destination.write_text("\n".join(sorted(imports)) + "\n\n" + "\n\n".join(bodies) + "\n", encoding="utf-8")
