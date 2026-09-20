# TaskHub Deployment

TaskHub deployment starts with one Seed controller and one preloaded unified
Node image. The Seed is the control plane; role-selected Node containers provide
execution, test and preproduction workloads.

Use the immutable release Compose and cross-platform scripts in `deploy/release`.
Read `ubuntu.md` or `windows-docker-desktop.md` for installation, upgrade and
recovery. `seed-node.md` records the earlier rapid-development Alpha environment.

The Web console creates role-selected containers only on the Seed Docker host.
Online initialization preloads the Node image; offline bundles import the same
image before the operator creates a node.
