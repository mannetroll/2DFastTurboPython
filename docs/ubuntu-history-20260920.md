# Ubuntu command log — 20 September 2026

Categorized from the 679-line shell-history export. The work covers Ubuntu 26.04,
an NVIDIA RTX 3090, FastTurbo development, and desktop/network setup.

Repeated commands are consolidated, with working-directory notes and one-line
commands where possible. Typos, incomplete commands, pasted output and known
unsupported variants have been removed. Configuration heredocs are preserved.
This is a historical log, grouped by task; command outcomes were not recorded.

## Contents

- [Native FastTurbo: build and launch](#native-fastturbo-build-and-launch)
- [Python GUI and Qt setup](#python-gui-and-qt-setup)
- [NVIDIA, CUDA and GPU monitoring](#nvidia-cuda-and-gpu-monitoring)
- [Benchmarks and reference experiments](#benchmarks-and-reference-experiments)
- [Python wheels and isolated installs](#python-wheels-and-isolated-installs)
- [Git repositories and workflow](#git-repositories-and-workflow)
- [SSH and GitHub access](#ssh-and-github-access)
- [Codex CLI](#codex-cli)
- [GNOME remote desktop](#gnome-remote-desktop)
- [Networking and Ethernet](#networking-and-ethernet)
- [Ubuntu maintenance and boot checks](#ubuntu-maintenance-and-boot-checks)
- [Steam and 32-bit graphics libraries](#steam-and-32-bit-graphics-libraries)
- [Shell navigation, files and evidence](#shell-navigation-files-and-evidence)
- [Cleanup and process control](#cleanup-and-process-control)

## Native FastTurbo: build and launch

### Release GUI build

From the repository root. These are the final GUI build commands in the export; `86` is the RTX 3090 CUDA architecture.

```bash
cd ~/github/2DFastTurboPython
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DFASTTURBO_BUILD_NATIVE_GUI=ON
cmake --build build-linux --target fastturbo-linux -j2
./fastturbo-linux
```

### Earlier full native build

From the repository root. The history also used this configuration and built all default targets.

```bash
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build-linux -j2
```

### Launcher, executable and dependencies

From the repository root. The root launcher and the built executable were inspected separately.

```bash
file fastturbo-linux
more fastturbo-linux
./build-linux/fastturbo-linux
ll ./build-linux/fastturbo-linux
file ./build-linux/fastturbo-linux
ldd ./build-linux/fastturbo-linux
```

### Copy to the user command path

From the repository root. Refresh the copied executable after a rebuild.

```bash
ll ~/.local/bin
cp ./build-linux/fastturbo-linux ~/.local/bin/
fastturbo-linux
```

## Python GUI and Qt setup

### Install uv and reload the shell

```bash
sudo apt install curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### Launch from the Python project

From the Python project directory. Use the default launch or explicitly select XCB/XWayland, which the Linux GPU canvas requires.

```bash
cd ~/github/2DFastTurboPython/python
uv run 2dfastturbo
QT_QPA_PLATFORM=xcb uv run 2dfastturbo
```

### Qt/XCB dependencies and display diagnostics

Run the `ldd` command from `~/github/2DFastTurboPython/python`.

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1 libxcb-xkb1
echo $DISPLAY
echo $WAYLAND_DISPLAY
ldd .venv/lib/python3.14/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"
```

## NVIDIA, CUDA and GPU monitoring

### GPU and graphics information

```bash
nvidia-smi
vulkaninfo --summary
sudo apt install nvtop
nvtop
```

### Continuous monitoring

```bash
nvidia-smi --loop=1
nvidia-smi --query-gpu=temperature.gpu,temperature.memory,power.draw,clocks.sm,clocks.mem --format=csv -l 1
nvidia-smi dmon -s pucvmet
```

### CUDA compiler and package inspection

These include the recorded Ubuntu CUDA-toolkit install and the subsequent compiler/package checks. The log does not contain an explicit CUDA 13.3 installer command.

```bash
nvcc --version
which nvcc
ls -ld /usr/local/cuda*
sudo apt install nvidia-cuda-toolkit
dpkg -S /usr/bin/nvcc
dpkg -l | grep -Ei 'cuda|nvidia-cuda' | grep '^ii'
readlink -f /usr/bin/nvcc
```

## Benchmarks and reference experiments

### Native 2dbench

From the FastTurbo repository root.

```bash
cd ~/github/2DFastTurboPython
./build-linux/2dbench
find . -name 2dbench
file build-linux/reference-linux/fastturbo_reference
```

### CNAB2 reference runs

From the repository root. Retains both the explicit `SCIPYTURBO_SEED=1` run and the run without that environment override.

```bash
SCIPYTURBO_SEED=1 ./build-linux/reference-linux/fastturbo_reference 512 10000 10 1001 0.25 cuda 100 PAO CNAB2
./build-linux/reference-linux/fastturbo_reference 512 10000 10 1001 0.25 cuda 100 PAO CNAB2
```

From `~/github`. The second invocation uses the reference executable’s default settings.

```bash
SCIPYTURBO_SEED=1 ./2DFastTurboPython/build-linux/reference-linux/fastturbo_reference 512 10000 10 1001 0.25 cuda 100 PAO CNAB2
SCIPYTURBO_SEED=1 ./2DFastTurboPython/build-linux/reference-linux/fastturbo_reference
```

### Palinstrophy Python comparison

Python 3.13 comparison runs using `mannetroll-palinstrophy` 0.1.5.

```bash
uv run --python 3.13 --with mannetroll-palinstrophy[cuda]==0.1.5 sim
SCIPYTURBO_SEED=1 uv run --python 3.13 --with 'mannetroll-palinstrophy[cuda]==0.1.5' sim 512 10000 10 1001 0.25 auto 100 PAO CNAB2
```

### cudaturbo build and memory experiments

Clone from `~/github`, then build and run inside `~/github/cudaturbo`.

```bash
git clone git@github.com:mannetroll/cudaturbo.git
cd cudaturbo/
make
./mem_cuda
```

Additional runs and recorded memory-sweep inspection from `~/github/cudaturbo`.

```bash
./mem_cuda 16384 1E7 4 1000000 0.1 100 0 CNAB2
./mem_cuda 16000 1E7 4 1000000 0.1 100 0 CNAB2
ll tools/
more tools/mem_sweep_RTX3090.csv
```

Alternative relative paths: `./cudaturbo/mem_cuda` from `~/github`, or `../cudaturbo/mem_cuda` from the FastTurbo repository root.

```bash
./cudaturbo/mem_cuda
../cudaturbo/mem_cuda
```

## Python wheels and isolated installs

### Build and inspect wheels

From `~/github/2DFastTurboPython/python`. The export includes wheel versions 0.1.1 and 0.1.2.

```bash
cd ~/github/2DFastTurboPython/python
uv run fastturbo-build --force
uv build --wheel
ll dist/
uv tool run auditwheel show dist/fastturbo-0.1.1-py3-none-linux_x86_64.whl
```

### Install the 0.1.1 wheel in a temporary environment

The recorded environment was `/tmp/fastturbo-test`; its removal command is retained under Cleanup.

```bash
cd /tmp/
uv venv --python 3.14 fastturbo-test
source fastturbo-test/bin/activate
uv pip install ~/github/2DFastTurboPython/python/dist/fastturbo-0.1.1-py3-none-linux_x86_64.whl
QT_QPA_PLATFORM=xcb 2dfastturbo
```

### Run the 0.1.2 wheel in isolation

Start in `~/github/2DFastTurboPython/python`, then enter `dist/`.

```bash
cd dist/
uvx --isolated --no-build --from ./fastturbo-0.1.2-py3-none-linux_x86_64.whl 2dfastturbo
uvx --isolated --no-build --from ./fastturbo-0.1.2-py3-none-linux_x86_64.whl 2dbench
```

### Track built wheel files

From the Python project directory. These are the two historical wheel versions force-added to Git, not a new packaging policy.

```bash
git add --force dist/fastturbo-0.1.1-py3-none-linux_x86_64.whl
git add --force dist/fastturbo-0.1.2-py3-none-linux_x86_64.whl
```

## Git repositories and workflow

### Install and configure Git

The heredoc below is the configuration written in the original history; it replaces `~/.gitconfig`.

```bash
sudo apt install git
git config --global --list
cat > ~/.gitconfig <<'EOF'
[user]
    email = torbjorn.sjogren@gmail.com
    name = Torbjörn Sjögren
[core]
    excludesfile = /home/tobbe/.gitignore_global
    autocrlf = input
[push]
    default = simple
[pull]
    rebase = true
EOF
```

### Clone FastTurbo

The original setup started in the home directory.

```bash
mkdir github
cd github/
git clone git@github.com:mannetroll/2DFastTurboPython.git
cd 2DFastTurboPython/
```

### Status and changes

From the FastTurbo repository root. The subshell checks the adjacent `cudaturbo` checkout.

```bash
git status
git status --short
git status --ignored
git branch
git branch --show-current
git log --oneline -5
git log -1 --oneline
git diff -- .gitignore
cat .gitignore
(cd ../cudaturbo/; git status)
```

### Branches, synchronization and baseline tag

Historical operations, grouped by purpose. `git switch -c IMGUI_LINUX` created the branch; the final history later returned to `main`.

```bash
git checkout LINUX
git checkout main
git switch main
git fetch
git pull
git pull --ff-only origin main
git push
git switch -c IMGUI_LINUX
git push -u origin IMGUI_LINUX
git tag h100-baseline
git push origin h100-baseline
```

### Staging and recorded commits

```bash
git add -A
git add .
```

Distinct commit commands from the history, in their original first-occurrence order:

```bash
git commit -a -m "CUDA 12.4"
git commit -a -m "CUDA 13.3"
git commit -a -m "Ubuntu 26.04"
git commit -m "Add headless Linux CUDA/VkFFT reference"
git commit -m "2dbench"
git commit -a -m "build-linux"
git commit -a -m "KM3"
git commit -a -m "CNAB2"
git commit -a -m "Dear ImGUI"
```

### Ignore rules and benchmark evidence

From the repository root.

```bash
git check-ignore -v python/evidence/2dbench/default.txt python/evidence/2dbench/default.json python/evidence/2dbench/REPORT.md
git ls-files python/evidence/2dbench/default.txt python/evidence/2dbench/default.json
ls -lh python/evidence/2dbench/
```

### Stash operations

`git stash clear` deletes the saved stash entries.

```bash
git stash
git stash list
git stash clear
```

## SSH and GitHub access

### SSH server

```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
systemctl status ssh
```

### Keys and GitHub authentication

The relative `.ssh` paths were inspected from the home directory.

```bash
ls -la ~/.ssh
more .ssh/authorized_keys
ll .ssh/
```

The recorded client configuration and key-file permissions. The heredoc replaces `~/.ssh/config`; no private-key contents appear in this log.

```bash
cat > ~/.ssh/config <<'EOF'
Host github.com
  AddKeysToAgent yes
  IdentityFile ~/.ssh/id_ed25519
EOF
chmod 600 ~/.ssh/config
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub
ssh -T git@github.com
```

## Codex CLI

### Node.js, npm and CLI installation

```bash
node --version
npm --version
sudo apt install nodejs
sudo apt install npm
sudo npm install -g @openai/codex@latest
codex --version
```

### Launch and resume commands

These are the exact launch/approval-mode variants recorded in the history, including one explicit session resume.

```bash
codex
codex resume
codex --yolo
codex --yolo resume
codex --ask-for-approval on-request
codex --ask-for-approval on-request resume 01a0a7a4-d136-77b2-8d6c-20e3636c6812
```

## GNOME remote desktop

The original notes associate port **3389** with Remote Login and **3390** with Desktop Sharing. System-service commands and user-service commands are grouped separately below.

### System service / Remote Login

The disable and enable commands were separate troubleshooting steps.

```bash
sudo systemctl status gnome-remote-desktop --no-pager
sudo systemctl is-enabled gnome-remote-desktop
sudo systemctl is-active gnome-remote-desktop
sudo grdctl --system status
sudo systemctl restart gnome-remote-desktop
sudo systemctl disable --now gnome-remote-desktop.service
sudo systemctl enable --now gnome-remote-desktop.service
sudo systemctl status gnome-remote-desktop.service --no-pager
```

### User service / Desktop Sharing

```bash
systemctl --user status gnome-remote-desktop --no-pager
systemctl --user status gnome-remote-desktop
grdctl status
systemctl --user daemon-reload
systemctl --user restart gnome-remote-desktop
```

### Listening ports and processes

```bash
sudo ss -lntp | grep 3389
sudo ss -lntp | grep 3390
ss -lntp | grep 3390
sudo ss -lntp | grep -E '3389|3390'
ps -ef | grep '[g]nome-remote-desktop'
```

### System logs

```bash
sudo journalctl -u gnome-remote-desktop -b --no-pager -n 100
sudo journalctl -u gnome-remote-desktop -b --since "2 minutes ago" --no-pager
sudo journalctl -u gnome-remote-desktop --since "2 minutes ago" --no-pager -o cat
sudo journalctl --since "2 minutes ago" --no-pager -o cat | grep -Ei 'gdm|gnome-remote|remote-desktop|rdp|mutter|session|wayland'
```

### User logs

```bash
journalctl --user -u gnome-remote-desktop --since "5 minutes ago" --no-pager
journalctl --user -u gnome-remote-desktop --since "2 minutes ago" --no-pager -o cat
journalctl --user -u gnome-remote-desktop --since "10 minutes ago" --no-pager -o cat
journalctl --user -u gnome-remote-desktop -b --no-pager -o cat | tail -80
```

### Sessions and package versions

```bash
loginctl list-sessions
loginctl show-user "$USER" -p State -p Sessions -p Display -p Linger
echo $XDG_SESSION_TYPE
echo $XDG_SESSION_ID
dpkg-query -W -f='${Package} ${Version}\n' gnome-remote-desktop gdm3 gnome-shell freerdp3-libs
```

## Networking and Ethernet

### Addresses, device state and firewall

```bash
hostname -I
ip -br addr
nmcli device status
hostnamectl
sudo ufw status
```

### eno1 link diagnostics

```bash
cat /sys/class/net/eno1/carrier
ip link show eno1
ip -4 addr show eno1
watch -n 1 cat /sys/class/net/eno1/carrier
sudo ethtool eno1
```

### Connection restart and link speed

Recorded changes to `netplan-eno1` and `eno1`, including the 1000 Mb/s full-duplex setting.

```bash
sudo nmcli con down netplan-eno1
sudo nmcli con up netplan-eno1
sudo ethtool -s eno1 speed 1000 duplex full
```

### Netplan configuration

The history records editing this YAML file, but does not include its contents.

```bash
cat /etc/netplan/99-eno1-link.yaml
sudo vi /etc/netplan/99-eno1-link.yaml
sudo chmod 600 /etc/netplan/99-eno1-link.yaml
sudo netplan generate && sudo netplan apply
```

## Ubuntu maintenance and boot checks

### Updates and power

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
sudo poweroff
```

### System, storage and locale

```bash
uname -a
locale -a | grep -i en_US
df -h .
top
date
```

### Firmware, Secure Boot and Windows boot entry

The GRUB configuration requires `sudo` to read on this machine.

```bash
fwupdmgr get-upgrades
mokutil --sb-state
sudo os-prober
sudo grep -i "Windows Boot Manager" /boot/grub/grub.cfg
```

## Steam and 32-bit graphics libraries

### Steam and i386 setup

`sudo apt update` was repeated after enabling i386; it is listed under Ubuntu maintenance.

```bash
sudo apt install steam-installer
dpkg --print-foreign-architectures
sudo dpkg --add-architecture i386
apt policy steam-libs-i386:i386
steam
```

### 32-bit NVIDIA libraries

Retains both the simulated package install and the actual install command.

```bash
dpkg -l | grep -E 'nvidia.*(libs|driver)' | head -30
apt policy nvidia-driver-libs:i386
sudo apt install --simulate libnvidia-gl-595:i386
sudo apt install libnvidia-gl-595:i386
dpkg -l | grep libnvidia-gl-595
```

## Shell navigation, files and evidence

### Navigation used in the history

Relative `cd` commands are retained as navigation history, not a sequence. Main working locations were `~/github/2DFastTurboPython`, its `python/` directory, the adjacent `cudaturbo/` checkout, and `~/Pictures/Screenshots`. Absolute project and `/tmp` navigation appears in the relevant sections above.

```bash
cd
cd ..
cd github/2DFastTurboPython/
cd python/
cd github/2DFastTurboPython/python/
cd Pictures/Screenshots/
cd reference-linux/
cd build-linux/
cd ../cudaturbo/
cd docs/
```

### Directory inspection

`ll` is the shell alias used in the original session. Relative paths refer to the directory being inspected at the time; `ll tests/` was used under `reference-linux/`, and `ll fastturbo-linux` under `build-linux/`.

```bash
ll
pwd
sudo apt install tree
tree
tree Pictures/
ll tests/
ll build-linux/
ls -lh build-linux/
ll native-linux/
tree native-linux/
ll fastturbo-linux
ll docs/
```

### Documentation and logs

`README.md` was viewed in multiple project directories; `CLI.md` was viewed in `python/`, and `2dbench.md` in `reference-linux/`. Paths prefixed with `reference-linux/`, plus `cli_linux.log` and the GUI task document, were used from the repository root.

```bash
more CLI.md
more README.md
more 2dbench.md
more reference-linux/README.md
more cli_linux.log
more FASTTURBO_LINUX_IMGUI_WINDOWS_STYLE.md
```

### Screenshot evidence

The copy command was run from `~/Pictures/Screenshots`.

```bash
cp Screenshot\ From\ 2026-09-17\ 23-01-11.png U2048.png
ll /home/tobbe/Pictures/Screenshots/U2048.png
```

### History export

The final redirect was run from `~/github/2DFastTurboPython/docs`, producing the source of this categorized log.

```bash
history
history > ubuntu-history-20260920.md
```

## Cleanup and process control

### Recorded directory removals

These commands remove directories; their scope depends on the working directory. They are historical cleanup steps, not part of every incremental build.

From `~/github/2DFastTurboPython/python`: remove the local virtual environment before recreating it.

```bash
rm -rf .venv/
```

From `/tmp`: remove the temporary wheel-test environment.

```bash
rm -rf fastturbo-test
```

From the FastTurbo repository root: remove all `build-*` matches, or only `build-linux/`.

```bash
rm -rf build-*
rm -rf build-linux/
```

### Jobs and termination

`%1` identifies shell job 1 in the current shell.

```bash
jobs
kill %1
```
