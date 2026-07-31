import json
from pathlib import Path

import pytest
from PIL import Image

import cat_layer_studio.services.project_service as project_service
from cat_layer_studio.models.project import CandidateState, Project
from cat_layer_studio.models.transform import Transform
from cat_layer_studio.services.project_service import (
    create_project,
    load_project,
    replace_master,
    save_project,
)


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


def test_normalised_project_preserves_original_and_reopens_working_master(tmp_path: Path) -> None:
    source = tmp_path / "large.png"
    Image.new("RGBA", (1254, 1254), (10, 20, 30, 128)).save(source)
    directory = tmp_path / "project"
    project = Project("Normalised cat", "", 512, 512)
    create_project(directory, project, source, normalise_master=True)

    payload = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    assert payload["master_original_path"] == "source/master_original.png"
    assert payload["master_working_path"] == "master/master_canvas.png"
    assert payload["master_path"] == payload["master_working_path"]
    assert payload["master_resize_scale"] == pytest.approx(512 / 1254)
    assert payload["master_normalisation_mode"] == "fit_inside"
    with Image.open(directory / payload["master_working_path"]) as working:
        assert working.size == (512, 512)
        assert working.mode == "RGBA"

    _, reopened = load_project(directory / "project.json")
    assert reopened.master_path == "master/master_canvas.png"


def test_legacy_project_still_opens(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGBA", (16, 16)).save(source / "master.png")
    (tmp_path / "project.json").write_text(
        json.dumps({"name": "Legacy", "master_path": "source/master.png"}),
        encoding="utf-8",
    )
    _, project = load_project(tmp_path / "project.json")
    assert project.master_original_path == "source/master.png"
    assert project.master_working_path == "source/master.png"


def test_replacing_master_backs_up_metadata_and_preserves_old_master(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (512, 512), (255, 0, 0, 255)).save(first)
    Image.new("RGBA", (1000, 500), (0, 255, 0, 255)).save(second)
    directory = tmp_path / "project"
    project = Project("Replace cat", "")
    create_project(directory, project, first)
    old_master = project.resolve(directory, project.master_path)

    replace_master(directory, project, second, normalise_master=True)

    assert old_master.exists()
    assert list((directory / "backups").glob("project-*.json"))
    assert project.master_path.startswith("master/master_canvas-")
    with Image.open(project.resolve(directory, project.master_path)) as working:
        assert working.size == (512, 512)


def test_failed_replacement_does_not_change_active_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGBA", (512, 512), (255, 0, 0, 255)).save(first)
    Image.new("RGBA", (1000, 500), (0, 255, 0, 255)).save(second)
    directory = tmp_path / "project"
    project = Project("Rollback cat", "")
    create_project(directory, project, first)
    original_metadata = (directory / "project.json").read_bytes()
    original_path = project.master_path

    def fail_save(*_args, **_kwargs) -> None:
        raise OSError("simulated metadata failure")

    monkeypatch.setattr(project_service, "save_project", fail_save)
    with pytest.raises(OSError, match="simulated"):
        replace_master(directory, project, second, normalise_master=True)

    assert project.master_path == original_path
    assert (directory / "project.json").read_bytes() == original_metadata
    assert not list((directory / "master").glob("master_canvas-*.png"))
