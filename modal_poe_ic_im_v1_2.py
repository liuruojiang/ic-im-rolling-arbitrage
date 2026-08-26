"""One-command Modal deployment for the persistent IC/IM v1.2 Poe server.

One-time setup (performed by the operator, never in source control):
  modal setup
  modal volume create poe-ic-im-v1-2-ledger
  Create Modal Dashboard secret "poe-ic-im-v1-2" with POE_ACCESS_KEY.
  modal deploy modal_poe_ic_im_v1_2.py
"""

from __future__ import annotations

import modal


APP_NAME = "poe-ic-im-v1-2"
VOLUME_NAME = "poe-ic-im-v1-2-ledger"
SECRET_NAME = "poe-ic-im-v1-2"
MOUNT = "/data"

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
secret = modal.Secret.from_name(SECRET_NAME)
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements-poe-v1.2-server.txt")
    .add_local_file(
        "poe_ic_im_mainline_v1_2_bot.py",
        "/root/poe_ic_im_mainline_v1_2_bot.py",
    )
    .add_local_file("poe_ic_im_v1_2_state.py", "/root/poe_ic_im_v1_2_state.py")
    .add_local_file("poe_ic_im_v1_2_server.py", "/root/poe_ic_im_v1_2_server.py")
    .env(
        {
            "PYTHONPATH": "/root",
            "ICIM_STATE_DIR": "/data/ic_im_v1_2",
            # Modal's scheduled function is the only ledger writer.  Web
            # containers reload the Volume and stay read-only to prevent a
            # scheduler/query race from creating divergent sequence files.
            "ICIM_DISABLE_INTERNAL_REFRESH": "1",
        }
    )
)


def _install_volume_barrier(server_module):
    """Reload before reads and commit every successful ledger append."""
    coordinator = server_module.coordinator
    if getattr(coordinator, "_modal_volume_barrier", False):
        return
    original_execute = coordinator.execute_query
    original_health = coordinator.health

    def execute_with_volume_barrier(*args, **kwargs):
        volume.reload()
        kwargs["persist_confirmed"] = False
        return original_execute(*args, **kwargs)

    coordinator.execute_query = execute_with_volume_barrier

    def health_with_volume_barrier():
        volume.reload()
        return original_health()

    coordinator.health = health_with_volume_barrier
    coordinator._modal_volume_barrier = True


@app.function(
    image=image,
    volumes={MOUNT: volume},
    secrets=[secret],
    timeout=180,
    max_containers=1,
)
@modal.concurrent(max_inputs=4)
@modal.asgi_app()
def poe_server():
    import poe_ic_im_v1_2_server as server

    _install_volume_barrier(server)
    return server.app


@app.function(
    image=image,
    volumes={MOUNT: volume},
    secrets=[secret],
    timeout=180,
    retries=2,
    schedule=modal.Cron("20,50 7-10 * * 1-5"),
)
def refresh_close_ledger() -> dict[str, object]:
    """Retry every 30 minutes from 15:20 to 18:50 Beijing on weekdays."""
    import poe_ic_im_v1_2_server as server

    volume.reload()
    server.coordinator.catch_up_until_current(max_sessions=4)
    volume.commit()
    return server.coordinator.health()
