from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_release_workflow():
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    with workflow_path.open() as workflow_file:
        return yaml.safe_load(workflow_file)


def test_release_workflow_runs_for_version_tags():
    workflow = load_release_workflow()
    trigger = workflow.get("on", workflow.get(True))

    assert trigger["push"]["tags"] == ["v*"]


def test_release_workflow_can_write_attestations_and_release_assets():
    workflow = load_release_workflow()

    assert workflow["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }


def test_release_workflow_attests_every_published_artifact_type():
    workflow = load_release_workflow()
    steps = workflow["jobs"]["release"]["steps"]
    tag_check_step = next(
        step
        for step in steps
        if step["name"] == "Verify tag matches package version"
    )
    attest_step = next(
        step
        for step in steps
        if step["name"] == "Generate provenance attestations"
    )

    assert "tomllib" in tag_check_step["run"]
    assert "GITHUB_REF_NAME" in tag_check_step["run"]
    assert attest_step["uses"] == "actions/attest@v4"
    subject_path = attest_step["with"]["subject-path"]
    assert "dist/*.tar.gz" in subject_path
    assert "dist/*.whl" in subject_path
    assert "dist/SHA256SUMS" in subject_path


def test_release_workflow_publishes_the_same_attested_files():
    workflow = load_release_workflow()
    steps = workflow["jobs"]["release"]["steps"]
    upload_step = next(
        step for step in steps if step["name"] == "Upload workflow artifacts"
    )
    release_step = next(
        step
        for step in steps
        if step["name"] == "Publish GitHub release assets"
    )

    assert upload_step["with"]["path"] == "dist/*"
    assert release_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "gh release create" in release_step["run"]
    assert "gh release upload" in release_step["run"]
    assert "dist/*" in release_step["run"]


def test_release_docs_include_consumer_verification_steps():
    docs = (ROOT / "docs" / "release-provenance.md").read_text()

    assert "gh attestation verify" in docs
    assert "orchestration-agent/AgentOrchestration" in docs
    assert "source repository" in docs
    assert "source revision" in docs
    assert "build workflow" in docs
    assert "subject digest" in docs
    assert "sha256sum -c SHA256SUMS" in docs
