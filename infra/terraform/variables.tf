variable "aws_region" {
  description = "AWS region for the VM."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = <<-EOT
    EC2 instance type. The full stack is 9 application containers plus
    Postgres, Redis, Jaeger and Caddy — t3.small (2 GiB RAM) is tight,
    t3.medium (4 GiB) is the smallest size this was sized against (not
    load-tested; see ADR-0020).
  EOT
  type        = string
  default     = "t3.medium"
}

variable "root_volume_gb" {
  description = "Root EBS volume size — container images plus Postgres/Jaeger data on one disk (no separate data volume; see ADR-0020's known gaps)."
  type        = number
  default     = 30
}

variable "ssh_public_key" {
  description = "Your SSH public key (contents of e.g. ~/.ssh/id_ed25519.pub) — the only way to reach the instance; there is no console password."
  type        = string
  # No default: an empty key would mean nobody can SSH in at all.
}

variable "allowed_ssh_cidr" {
  description = <<-EOT
    CIDR allowed to reach port 22, e.g. "203.0.113.4/32" for just your IP.
    No default on purpose — defaulting this to 0.0.0.0/0 would open SSH to
    the entire internet, which is exactly the mistake a default exists to
    prevent (same "fail fast rather than silently insecure" reasoning as
    GATEWAY_JWT_SECRET having no default — see ADR-0010).
  EOT
  type        = string
}

variable "domain_name" {
  description = <<-EOT
    DNS name that will point at this instance, e.g. "flowguard.example.com".
    Required: Caddy requests a Let's Encrypt certificate for exactly this
    name on first start, and that request fails until its DNS A record
    resolves to the instance's IP — see the setup guide for the ordering
    (provision, point DNS at the output IP, then deploy).
  EOT
  type        = string
}

variable "project" {
  description = "Resource name/tag prefix."
  type        = string
  default     = "flowguard"
}
