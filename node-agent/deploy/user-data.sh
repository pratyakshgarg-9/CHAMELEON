#!/bin/bash
# EC2 launch-time setup for a CHAMELEON node-agent host (Ubuntu 22.04/24.04).
# Installs Docker and Tailscale. Does NOT join the tailnet automatically —
# that's done interactively after boot (`sudo tailscale up`), so no auth key
# needs to be baked into this script or the AMI.
set -euxo pipefail

apt-get update -y
apt-get install -y ca-certificates curl gnupg

# Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker ubuntu

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled

# Git (to pull the node-agent code post-boot)
apt-get install -y git
