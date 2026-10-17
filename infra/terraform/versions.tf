# Pinned so `terraform init` resolves the same provider version every
# time — see docs/decisions/ADR-0020 for why this phase is built and
# validated (fmt, init, validate) but deliberately not applied: no real
# cloud account was available, and `apply` creates real, billed
# infrastructure that only the account owner should authorize.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
