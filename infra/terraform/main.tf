provider "aws" {
  region = var.aws_region
}

# Canonical's own published Ubuntu 24.04 LTS AMI, looked up by name rather
# than a hard-coded AMI ID — AMI IDs are region- and date-specific, so a
# hard-coded one silently goes stale or stops existing in another region.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "flowguard" {
  key_name   = "${var.project}-deploy"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "flowguard" {
  name        = "${var.project}-sg"
  description = "FlowGuard single-VM deployment: SSH from one CIDR, HTTP/HTTPS from anywhere (Caddy terminates TLS; everything else is internal-only, see ADR-0020)."

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }
  ingress {
    description = "HTTP (ACME challenge + redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "all outbound (image pulls, Lets Encrypt, Anthropic/PayPal APIs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project}-sg" }
}

resource "aws_instance" "flowguard" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.flowguard.key_name
  vpc_security_group_ids = [aws_security_group.flowguard.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    project = var.project
  })
  # Changing user_data on an existing instance doesn't re-run it — Terraform
  # would otherwise show a confusing "no-op" diff forever after the first
  # apply. Provisioning is a one-time bootstrap; see cloud-init.yaml.tftpl's
  # own comment for why redeploys don't go through here at all.
  user_data_replace_on_change = false

  tags = { Name = var.project }

  lifecycle {
    # The volume holds Postgres's data directory — losing it on an
    # unrelated instance change (e.g. switching instance_type) would be a
    # production-data-loss footgun for a one-VM deployment with no backup
    # strategy yet (see ADR-0020's known gaps).
    prevent_destroy = true
  }
}

resource "aws_eip" "flowguard" {
  instance = aws_instance.flowguard.id
  domain   = "vpc"
  tags     = { Name = "${var.project}-eip" }
}
