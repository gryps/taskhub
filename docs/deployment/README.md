# TaskHub Deployment

TaskHub deployment starts with one Seed controller and one preloaded unified
Node image. The Seed is the control plane; role-selected Node containers provide
execution, test and preproduction workloads.

Use the immutable release Compose and cross-platform scripts in `deploy/release`.
Read `ubuntu.md` or `windows-docker-desktop.md` for installation, upgrade and
recovery. `seed-node.md` records the earlier rapid-development Alpha environment.

The Web console can create role-selected containers on the Seed Docker host or
an admitted remote Linux Docker host. It distributes the Node image by registry,
mirror, remote cache or SSH `docker load`, then verifies architecture and image
identity before starting the container.
