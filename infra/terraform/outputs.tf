output "public_ip" {
  description = "Elastic IP — point domain_name's DNS A record at this, then deploy."
  value       = aws_eip.flowguard.public_ip
}

output "ssh_command" {
  description = "Connect to run the first deploy or to inspect the box directly."
  value       = "ssh ubuntu@${aws_eip.flowguard.public_ip}"
}

output "ssh_keyscan_command" {
  description = "Run once after the instance is reachable; its output is the DEPLOY_HOST_KEY GitHub secret (see the setup guide) — pinning the real host key instead of trust-on-first-connect."
  value       = "ssh-keyscan -t ed25519 ${aws_eip.flowguard.public_ip}"
}
