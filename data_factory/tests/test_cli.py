"""CLI tests: dfac subcommands against a temp data dir."""

from conftest import make_import_rows, write_eval_items, write_import_manifest

from data_factory.cli import main


def _run(*argv):
    code = main(list(argv))
    assert code == 0, f"dfac {' '.join(argv)} failed"
    return code


def test_cli_init(factory_kwargs, capsys):
    _run("--data-dir", str(factory_kwargs["data_dir"]), "init")
    out = capsys.readouterr().out
    assert "backend:" in out
    assert "schema_check" in out


def test_cli_capability_and_strategy(factory_kwargs, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    _run(*base, "capability", "add", "chart_fact_qa")
    _run(*base, "capability", "list")
    assert "chart_fact_qa" in capsys.readouterr().out
    _run(*base, "strategy", "add", "fact-qa", "--domain", "chart_fact_qa")
    _run(*base, "strategy", "list")
    assert "fact-qa" in capsys.readouterr().out


def test_cli_dataset_and_workflow_and_run(factory_kwargs, tmp_path, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    manifest = write_import_manifest(tmp_path, make_import_rows(count=3))
    _run(*base, "capability", "add", "cd")
    _run(*base, "strategy", "add", "st", "--domain", "cd")
    capsys.readouterr()
    _run(
        *base,
        "dataset",
        "add",
        "qa",
        "--source-type",
        "import",
        "--manifest",
        str(manifest),
    )
    ds_id = capsys.readouterr().out.strip()
    _run(
        *base,
        "workflow",
        "define",
        "wf",
        "--strategy",
        "st",
        "--stages",
        "schema_check",
        "--stages",
        f'publish={{"dataset_id": "{ds_id}"}}',
    )
    wf_id = capsys.readouterr().out.strip()
    _run(*base, "run", "start", wf_id, "--dataset", ds_id)
    assert "status=succeeded" in capsys.readouterr().out
    _run(*base, "run", "list")
    assert wf_id in capsys.readouterr().out


def test_cli_workflow_validate(factory_kwargs, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    _run(*base, "capability", "add", "cd")
    _run(*base, "strategy", "add", "st", "--domain", "cd")
    capsys.readouterr()
    _run(*base, "workflow", "define", "wf", "--strategy", "st", "--stages", "dedup")
    wf_id = capsys.readouterr().out.strip()
    _run(*base, "workflow", "validate", wf_id)
    assert "chain:" in capsys.readouterr().out


def test_cli_stage_list_and_run(factory_kwargs, tmp_path, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    _run(*base, "stage", "list")
    assert "schema_check" in capsys.readouterr().out
    manifest = write_import_manifest(tmp_path, make_import_rows(count=2))
    _run(*base, "stage", "run", "dedup", "--input", str(manifest))
    assert "duplicate" in capsys.readouterr().out


def test_cli_lineage(factory_kwargs, tmp_path, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    manifest = write_import_manifest(tmp_path, make_import_rows(count=2))
    _run(*base, "capability", "add", "cd")
    _run(*base, "strategy", "add", "st", "--domain", "cd")
    capsys.readouterr()
    _run(
        *base,
        "dataset",
        "add",
        "qa",
        "--source-type",
        "import",
        "--manifest",
        str(manifest),
    )
    ds_id = capsys.readouterr().out.strip()
    _run(
        *base,
        "workflow",
        "define",
        "wf",
        "--strategy",
        "st",
        "--stages",
        "schema_check",
        "--stages",
        f'publish={{"dataset_id": "{ds_id}"}}',
    )
    wf_id = capsys.readouterr().out.strip()
    _run(*base, "run", "start", wf_id, "--dataset", ds_id)
    capsys.readouterr()
    _run(*base, "lineage", "--dataset", f"{ds_id}@1")
    assert '"version": 1' in capsys.readouterr().out


def test_cli_models(factory_kwargs, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    _run(
        *base,
        "model",
        "register",
        "m1",
        "--backend",
        "api",
        "--base-url",
        "http://127.0.0.1:1",
    )
    model_id = capsys.readouterr().out.strip()
    _run(*base, "model", "list")
    assert "m1" in capsys.readouterr().out
    _run(*base, "model", "check", model_id)
    assert "failed" in capsys.readouterr().out


def test_cli_eval_flow(factory_kwargs, tmp_path, mock_llm, capsys):
    base = ["--data-dir", str(factory_kwargs["data_dir"])]
    evals = write_eval_items(tmp_path, count=4)
    _run(
        *base,
        "model",
        "register",
        "mock",
        "--backend",
        "api",
        "--base-url",
        mock_llm,
        "--model-id",
        "mock",
    )
    model_id = capsys.readouterr().out.strip()
    _run(*base, "model", "check", model_id)
    capsys.readouterr()
    _run(*base, "evalset", "import", "chart-eval", "--file", str(evals))
    es_id = capsys.readouterr().out.strip().split()[0]
    _run(*base, "eval", "run", es_id, "--model", model_id)
    out = capsys.readouterr().out
    assert "status=succeeded" in out
    _run(*base, "report", "list")
    report_id = capsys.readouterr().out.splitlines()[2].split()[0]
    _run(*base, "report", "show", report_id)
    assert "badcase_count" in capsys.readouterr().out
    _run(*base, "report", "export", report_id, "--out", str(tmp_path / "r.json"))
    assert (tmp_path / "r.json").is_file()


def test_cli_error_handling(factory_kwargs, capsys):
    code = main(
        [
            "--data-dir",
            str(factory_kwargs["data_dir"]),
            "run",
            "start",
            "wf_missing",
            "--dataset",
            "ds_missing",
        ]
    )
    assert code == 1
    assert "error:" in capsys.readouterr().err
