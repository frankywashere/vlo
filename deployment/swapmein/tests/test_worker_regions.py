"""GPU migration must not silently move the audio worker or task queues."""
import deploy


def test_deployment_discovers_and_checks_the_workers_in_their_own_regions(monkeypatch):
    urls, commands = [], []

    def read(url):
        urls.append(url)
        return {"scaling": {"scalingMode": "MANUAL", "manualInstanceCount": 0},
                "template": {"scaling": {"maxInstanceCount": 1}}}

    def command(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("run", "services", "get-iam-policy"):
            return {"bindings": []}
        return {"status": {"url": "https://" + args[3] + ".run.app"}}

    monkeypatch.setattr(deploy, "google_get", read)
    monkeypatch.setattr(deploy, "gcloud", command)
    workers = deploy.worker_details()
    assert "/locations/us-central1/services/proof-gpu" in urls[0]
    assert "/locations/us-east4/services/proof-audio" in urls[1]
    assert all("--region=us-central1" in args for args in commands if args[3] == "proof-gpu")
    assert all("--region=us-east4" in args for args in commands if args[3] == "proof-audio")
    env = deploy.deployment_env(workers, ["https://recastfx.com"], "https://gateway.run.app")
    assert env["GPU_REGION"] == "us-central1" and env["REGION"] == "us-east4"


def test_scaler_permission_checks_follow_each_worker_region(monkeypatch):
    resources = []

    def command(*args, **kwargs):
        resources.append(args[2])
        return [{"name": name, "customRolesSupportLevel": "SUPPORTED"}
                for name in ["run.services.get", "run.services.update"]]

    monkeypatch.setattr(deploy, "gcloud", command)
    deploy.verify_scaler_scope()
    assert resources == [
        f"//run.googleapis.com/projects/{deploy.PROJECT}/locations/us-central1/services/proof-gpu",
        f"//run.googleapis.com/projects/{deploy.PROJECT}/locations/us-east4/services/proof-audio",
    ]
