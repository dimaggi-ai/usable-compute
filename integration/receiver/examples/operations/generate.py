"""Generate synthetic NVIDIA and TPU end-to-end admission replays; no network."""

import importlib.util
from pathlib import Path
from dimaggi_receiver.assessment import assess
from dimaggi_receiver.telemetry import nvidia_smi, google_tpu, TPU_PREFIX
from dimaggi_receiver.source_watch import collect, compare, review_queue
from dimaggi_receiver.jsonio import canonical, digest, dumps


def generate(output):
    spec = importlib.util.spec_from_file_location(
        "infra_examples", Path(__file__).parents[1] / "infrastructure/generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for provider in ("nvidia", "google"):
        folder = output / provider
        folder.mkdir(parents=True, exist_ok=True)
        registry, request = module.fixtures()
        profile = registry["profiles"][0]
        pool = request["pools"][0]
        workload = request["workloads"][0]
        profile.update(
            id="synthetic." + provider + ".test-v1",
            provider=provider,
            device_unit="gpu" if provider == "nvidia" else "physical_chip",
            archetypes=["inference"],
            memory_bytes_per_device=1024**3,
        )
        profile["stack"]["driver"] = (
            "580.1" if provider == "nvidia" else "fixture-libtpu"
        )
        pool.update(
            profile_id=profile["id"],
            device_unit=profile["device_unit"],
            stack=profile["stack"],
        )
        pool["headroom"]["devices"] = 1
        workload.update(allowed_profiles=[profile["id"]], archetype="inference")
        config = dict(
            schema="dimaggi-source-watch-config/v1",
            sources=[
                dict(
                    id="fixture-source",
                    url=(
                        "https://docs.nvidia.com/deploy/nvidia-smi/"
                        if provider == "nvidia"
                        else "https://docs.cloud.google.com/tpu/docs/monitor-tpu"
                    ),
                    profile_ids=[profile["id"]],
                    max_bytes=1024,
                )
            ],
        )
        now = "2026-09-20T12:00:01Z"
        source = collect(
            config, now, lambda *a, **k: (b"SYNTHETIC SOURCE FIXTURE", "text/plain")
        )
        queue = review_queue(config, compare(config, source, source))
        meta = dict(
            source_id=pool["source_id"],
            source_epoch=pool["epoch"],
            target_id=pool["target_id"],
            observed_at=pool["observed_at"],
            evidence_class="synthetic",
        )
        if provider == "nvidia":
            raw = b"GPU-01234567-abcd, NVIDIA Test GPU, 580.1, 1024, 256, 768, 20.125, 100, Disabled\n"
            snapshot = nvidia_smi(raw, meta, now)
            (folder / "input.csv").write_bytes(raw)
        else:
            identity = dict(
                project_id="fixture-project", zone="us-central1-a", instance_id="123"
            )
            series = []
            for metric, value in [
                ("memory_total", 1024**3),
                ("memory_used", 256 * 1024**2),
            ]:
                series.append(
                    dict(
                        metric=dict(
                            type=TPU_PREFIX + metric, labels={"accelerator_id": "0"}
                        ),
                        resource=dict(type="gce_instance", labels=identity),
                        metricKind="GAUGE",
                        valueType="INT64",
                        unit="By",
                        points=[
                            dict(
                                interval={"endTime": pool["observed_at"]},
                                value={"int64Value": str(value)},
                            )
                        ],
                    )
                )
            raw = canonical({"timeSeries": series, "unit": "By"})
            snapshot = google_tpu(raw, meta, identity, now)
            (folder / "input.json").write_bytes(raw)
            (folder / "identity.json").write_text(dumps(identity))
        entry = dict(
            pool_id=pool["id"],
            snapshot=snapshot,
            snapshot_digest=snapshot["snapshot_digest"],
            device_mapping={
                d["id"]: "fixture-physical-" + str(i)
                for i, d in enumerate(snapshot["devices"])
            },
        )
        evidence = dict(
            schema="dimaggi-assessment-evidence/v1",
            telemetry=[entry],
            source_queue=queue,
        )
        result = assess(
            registry, request, digest(registry), evidence, digest(evidence), now
        )
        assert result["status"] == "compatible" and not result["execution_authorized"]
        for name, value in [
            ("registry", registry),
            ("request", request),
            ("metadata", meta),
            ("snapshot", snapshot),
            ("evidence", evidence),
            ("assessment", result),
            (
                "pins",
                dict(
                    registry_digest=digest(registry),
                    evidence_digest=digest(evidence),
                    as_of=now,
                ),
            ),
        ]:
            (folder / (name + ".json")).write_text(dumps(value))


if __name__ == "__main__":
    generate(Path(__file__).parent)
