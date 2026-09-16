# Terraform architecture skeleton

This directory documents a possible AWS shape for the LeetCode Coach Service:

- private VPC subnets in two availability zones;
- a private PostgreSQL database;
- an ECS cluster for the application container;
- CloudWatch logs;
- future secret delivery through AWS Secrets Manager;
- a GitHub Actions deployment role trusted through GitHub OIDC.

This is an architecture skeleton, not a provisioned environment. `enable_resources` defaults to `false`. No state, credentials, backend, deployment role, ECS service, load balancer, Telegram webhook, or production DNS is included.

## Safe checks

```bash
terraform init -backend=false
terraform fmt -check
terraform validate
terraform plan -input=false -var='enable_resources=false'
```

Do not run `terraform apply` from this repository without a separate review covering IAM/OIDC trust, network egress, cost, backups, deletion protection, secret rotation, Telegram ingress, observability, and data retention.
