import json
from pathlib import Path

import pytest
from PIL import Image

from cat_layer_studio.models.project import CandidateState, Project
from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.project_service import create_project, load_project, save_project


def test_project_round_trip_is_self_contained_and_backed_up(tmp_path: Path) -> None:
    master = tmp_path / "master-input.png"
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(master)
    directory = tmp_path / "project"
    project = Project("Test cat", "")
    create_project(directory, project, master)
    project.candidate = CandidateState("source/candidate.png", Transform(x=1.25))
    Image.new("RGBA", (16, 16)).save(directory / "source" / "candidate.png")
    save_project(directory, project)
    _, loaded = load_project(directory / "project.json")
    assert loaded.to_dict() == project.to_dict()
    assert list((directory / "backups").glob("project-*.json"))
    assert (
        json.loads((directory / "project.json").read_text())["master_path"] == "source/master.png"
    )


def test_project_rejects_escaping_relative_paths(tmp_path: Path) -> None:
    project = Project("Unsafe", "../master.png")
    with pytest.raises(ValueError, match="escapes"):
        project.resolve(tmp_path, project.master_path)
