# TaskHub Deployment

TaskHub deployment starts with one seed controller and expands to dedicated
execution, acceptance, browser, and preproduction nodes. The seed controller is
the control plane; it is not a complete coding or browser-test node.

Read `seed-node.md` for the current alpha seed deployment. The Web console can
create role-selected containers on the Seed Docker host. SSH-based creation on
additional physical hosts remains planned work and must not be represented as
available until the acceptance criteria in `../requirements/seed-ssh-multihost.md`
are satisfied.
