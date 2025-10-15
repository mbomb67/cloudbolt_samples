# Copilot Instructions for AI Agents

## Project Overview
This repository is intended for managing Terraform configurations for an AWS multi-tier application. It is currently empty—no code, configuration, or documentation files are present yet.

## Guidance for AI Agents
- **Project Purpose:** This repo will contain infrastructure-as-code (IaC) for provisioning and managing AWS resources for a multi-tier application (e.g., VPC, subnets, EC2, RDS, etc.).
- **Expected Structure:** Organize Terraform code by environment (e.g., `dev/`, `prod/`) and/or by component (e.g., `network/`, `compute/`, `database/`). Use modules for reusable patterns.
- **Naming Conventions:** Use clear, descriptive names for resources and modules. Follow AWS and Terraform best practices for naming and tagging.
- **State Management:** Plan for remote state storage (e.g., S3 with DynamoDB locking) and document backend configuration.
- **Workflows:**
  - Use `terraform init`, `terraform plan`, and `terraform apply` for provisioning.
  - Use `terraform fmt` and `terraform validate` for formatting and validation.
  - Document any required environment variables or secrets in a `README.md` or example `.tfvars` file.
- **Collaboration:**
  - Use pull requests for changes.
  - Document module usage and input/output variables.
  - Add comments to complex resources or logic.

## Next Steps
- Scaffold the initial directory structure and add a `README.md` with project goals and setup instructions.
- Add example Terraform files for core AWS resources.

_This file should be updated as the project evolves to reflect new conventions, workflows, and architectural decisions._
