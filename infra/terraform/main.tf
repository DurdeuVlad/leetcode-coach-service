locals {
  name = var.project_name
}

resource "aws_vpc" "this" {
  count                = var.enable_resources ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = local.name
  }
}

resource "aws_subnet" "private" {
  count             = var.enable_resources ? length(var.availability_zones) : 0
  vpc_id            = aws_vpc.this[0].id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${local.name}-private-${count.index + 1}"
    Tier = "private"
  }
}

resource "aws_security_group" "database" {
  count       = var.enable_resources ? 1 : 0
  name        = "${local.name}-database"
  description = "Database access from the future application network only."
  vpc_id      = aws_vpc.this[0].id

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_ecs_cluster" "this" {
  count = var.enable_resources ? 1 : 0
  name  = local.name
}

resource "aws_cloudwatch_log_group" "app" {
  count             = var.enable_resources ? 1 : 0
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

resource "aws_db_subnet_group" "this" {
  count      = var.enable_resources ? 1 : 0
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  count                       = var.enable_resources ? 1 : 0
  identifier                  = local.name
  engine                      = "postgres"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  storage_encrypted           = true
  db_name                     = "leetcode_coach"
  username                    = var.database_master_username
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.this[0].name
  vpc_security_group_ids      = [aws_security_group.database[0].id]
  publicly_accessible         = false
  skip_final_snapshot         = true
}
