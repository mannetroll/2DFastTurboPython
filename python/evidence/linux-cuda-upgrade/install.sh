#!/usr/bin/env bash
# Native Ubuntu 26.04: NVIDIA toolkit only, preserving the installed driver.
set -euo pipefail
export LC_ALL=C
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
[[ $EUID == 0 ]]
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 ]]
[[ $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1) == 595.91.07 ]]
setup_dir=/home/tobbe/.cache/fastturbo-cuda-upgrade
log_dir=/var/log/fastturbo-cuda-upgrade
install -d -m 755 "$log_dir"
exec > >(tee "$log_dir/install.log") 2>&1
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' > "$log_dir/packages-before.tsv"
cd "$setup_dir"
echo 'f7f474b5f6a4adf987aa587920df00e713285958ef6a913dda1945a544a3099e  cuda-keyring_1.1-1_all.deb' | sha256sum --check
install -m 644 driver-pin /etc/apt/preferences.d/fastturbo-preserve-ubuntu-driver
dpkg -i cuda-keyring_1.1-1_all.deb
apt-get update -o Dir::Etc::sourcelist=sources.list.d/cuda-ubuntu2604-x86_64.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
packages=(cuda-toolkit-13-3=13.3.1-1 libpng-dev libopengl-dev)
apt-get --simulate --no-install-recommends --no-remove install "${packages[@]}" > "$log_dir/apt-plan.txt"
cat "$log_dir/apt-plan.txt"
if grep -Eq '^(Inst|Remv) (nvidia-|libnvidia-|linux-|xserver-xorg-video-nvidia|cuda-drivers|cuda-runtime|cuda-compat|libcuda1)' "$log_dir/apt-plan.txt"; then
    echo 'Refusing transaction: it would change a driver or kernel package.' >&2
    exit 1
fi
DEBIAN_FRONTEND=noninteractive apt-get --yes --no-install-recommends --no-remove install "${packages[@]}"
dpkg-query -W -f='${binary:Package}\t${Version}\t${db:Status-Status}\n' > "$log_dir/packages-after.tsv"
[[ $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1) == 595.91.07 ]]
/usr/local/cuda-13.3/bin/nvcc --version
echo 'Toolkit installation complete; NVIDIA driver remains 595.91.07.'
