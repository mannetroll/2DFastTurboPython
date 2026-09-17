# Native Ubuntu CUDA development environment for 2DFastTurbo.
# Installed with NVIDIA's ubuntu2604 cuda-toolkit-13-3 package.
export CUDA_HOME=/usr/local/cuda-13.3
export CUDA_PATH="$CUDA_HOME"
export CUDAToolkit_ROOT="$CUDA_HOME"
export CUDACXX="$CUDA_HOME/bin/nvcc"
export CUDAHOSTCXX=/usr/bin/g++
export NVCC_CCBIN=/usr/bin/g++
case ":$PATH:" in
    *:"$HOME/.local/bin":*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
case ":$PATH:" in
    *:"$CUDA_HOME/bin":*) ;;
    *) export PATH="$CUDA_HOME/bin:$PATH" ;;
esac
# Runtime libraries only: never add CUDA's link-time stubs to this path.
case ":${LD_LIBRARY_PATH:-}:" in
    *:"$CUDA_HOME/lib64":*) ;;
    *) export LD_LIBRARY_PATH="$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
esac
export FASTTURBO_LINUX_RENDERER=ON
