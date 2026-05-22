# Release Provenance Verification

Tagged releases are built by the trusted GitHub Actions release workflow. The
workflow publishes the Python wheel, Python source distribution, repository
source archive, and `SHA256SUMS` manifest as release assets. Every asset is
covered by a GitHub provenance attestation before it is uploaded.

The attestation is generated from the release workflow identity and binds the
asset name and digest to the build that produced it. Consumers should verify the
attestation before installing or redistributing a release artifact.

## Online Verification

Download the release asset, then verify it against this repository:

```bash
gh attestation verify ./agent_orchestrator-2.4.1-py3-none-any.whl \
  -R orchestration-agent/AgentOrchestration
```

Repeat the same command for the source distribution, source archive, and
`SHA256SUMS` file shipped with the release.

To inspect the provenance fields that the release workflow records:

```bash
gh attestation verify ./agent_orchestrator-2.4.1-py3-none-any.whl \
  -R orchestration-agent/AgentOrchestration \
  --format json \
  --jq '.[].verificationResult.statement.predicate'
```

Check that the predicate identifies:

- `orchestration-agent/AgentOrchestration` as the source repository
- the expected tag commit as the source revision
- the `Release` workflow as the build workflow
- a subject digest that matches the downloaded artifact

## Digest Check

Verify the asset digest locally before trusting the attestation result:

```bash
sha256sum -c SHA256SUMS
```

The digest check proves the downloaded files match the release manifest. The
attestation check proves the manifest and release assets were produced by the
repository workflow rather than by a manual rebuild with the same version.
