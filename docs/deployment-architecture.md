# Deployment architecture — target only

The repository currently describes and tests a Docker/Coolify deployment path. The AWS design below is a future option for demonstrating cloud-readiness; it is not live infrastructure.

## Target shape

1. GitHub Actions validates the `dev` integration branch and the protected `master` release path.
2. A manually approved workflow uses GitHub OIDC to assume a narrowly scoped AWS deployment role.
3. The role publishes an immutable application image and updates an ECS service in private subnets.
4. PostgreSQL runs privately with encrypted storage and credentials managed outside Git.
5. CloudWatch receives structured application logs and basic service/database metrics.
6. Telegram reaches a deliberately documented HTTPS ingress; no public database endpoint is required.

The Terraform files are deliberately guarded by `enable_resources = false`. They are meant to make the intended boundaries reviewable without pretending that deployment, security hardening, or production verification has happened.

## Target SLOs — not achieved claims

These are review targets for a future deployment, not current measurements:

- health endpoint availability: 99.5% monthly;
- webhook request success: 99% for valid requests;
- p95 application response time: under 1 second for non-LLM health/API paths;
- recovery objective: restore the application from an immutable image and managed database backup within one hour.

The current repository has no production evidence for these targets. Keep them labelled as targets until monitoring and incident records exist.
