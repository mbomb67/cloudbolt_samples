#!/bin/bash

# Update system
apt update -y && apt upgrade -y

# Disable swap
swapoff -a
sed -i '/swap/d' /etc/fstab

# Enable br_netfilter for Kubernetes networking
modprobe br_netfilter
echo '1' > /proc/sys/net/bridge/bridge-nf-call-iptables
echo '1' > /proc/sys/net/bridge/bridge-nf-call-ip6tables

echo 'br_netfilter' >> /etc/modules-load.d/k8s.conf
echo 'net.bridge.bridge-nf-call-iptables=1' >> /etc/sysctl.d/k8s.conf
echo 'net.bridge.bridge-nf-call-ip6tables=1' >> /etc/sysctl.d/k8s.conf
sysctl --system

# Install container runtime (containerd)
apt install -y apt-transport-https ca-certificates curl software-properties-common
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
# add-apt-repository -y "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
apt update -y
apt install -y containerd.io

# Configure containerd
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd

# Add Kubernetes repository
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
apt update -y

# Install Kubernetes components
apt install -y kubelet kubeadm kubectl
systemctl enable kubelet --now

# Mark the packages as held back to prevent automatic installation, upgrade, or removal
apt-mark hold kubeadm kubelet kubectl

# Enable IP forwarding
echo '1' > /proc/sys/net/ipv4/ip_forward

echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf
sysctl -p

# Pull the recommended sandbox image
ctr image pull registry.k8s.io/pause:3.10
ctr image tag registry.k8s.io/pause:3.10 registry.k8s.io/pause:3.6

# Initialize the Kubernetes cluster
kubeadm init --pod-network-cidr=192.168.0.0/16

# Check if kubeadm init was successful
if [ $? -ne 0 ]; then
    echo "kubeadm init failed. Please check the output for errors."
    exit 1
fi

# Configure kubectl for the root user
mkdir -p $HOME/.kube
cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
chown $(id -u):$(id -g) $HOME/.kube/config

# Configure kubectl for the ubuntu user
mkdir -p /home/ubuntu/.kube
cp -i /etc/kubernetes/admin.conf /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config

# Deploy a CNI (Calico in this case)
kubectl apply -f https://docs.projectcalico.org/manifests/calico.yaml --validate=false

# Print the join command for worker nodes
kubeadm token create --print-join-command > /tmp/kubeadm_join_command.sh

echo "Control plane setup is complete. Join command saved to /tmp/kubeadm_join_command.sh"