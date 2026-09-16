output "vpc_id" {
  description = "Future VPC identifier."
  value       = try(aws_vpc.this[0].id, null)
}

output "ecs_cluster_name" {
  description = "Future ECS cluster name."
  value       = try(aws_ecs_cluster.this[0].name, null)
}

output "database_endpoint" {
  description = "Future private database endpoint."
  value       = try(aws_db_instance.postgres[0].address, null)
}
