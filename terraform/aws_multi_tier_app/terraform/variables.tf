variable "db_username" {
  description = "Master username for the RDS database."
  type        = string
}

variable "db_password" {
  description = "Master password for the RDS database."
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "Instance class for the RDS database (e.g., db.t3.micro, db.t3.small, etc.)."
  type        = string
  default     = "db.t3.micro"
}

variable "db_engine" {
  description = "Database engine for RDS. Valid options: mysql, postgres, mariadb, oracle-se2, sqlserver-ee, sqlserver-se, sqlserver-ex, sqlserver-web."
  type        = string
  default     = "mysql"
}

variable "db_allocated_storage" {
  description = "Allocated storage (in GB) for the RDS database."
  type        = number
  default     = 20
}

variable "template_image_id" {
  description = "AMI ID for the EC2 launch template."
  type        = string
  default     = "ami-0c94855ba95c71c99"
}

variable "template_instance_type" {
  description = "Instance type for the EC2 launch template (e.g., t3.micro, t3.small, etc.)."
  type        = string
  default     = "t3.micro"
}
variable "app_name" {
  description = "Application name for resource naming and tagging."
  type        = string
}

variable "env_name" {
  description = "Environment name (e.g., dev, prod) for resource naming and tagging."
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy resources in."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "owner" {
  description = "Owner tag for resources."
  type        = string
}

variable "group" {
  description = "Group tag for resources."
  type        = string
}

variable "cost_center" {
  description = "Cost Center tag for resources."
  type        = string
}
