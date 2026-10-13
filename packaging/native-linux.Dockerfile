FROM nvidia/cuda:13.1.1-devel-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake ninja-build g++ libx11-dev libgl-dev libglx-dev libpng-dev zlib1g-dev \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location https://developer.download.nvidia.com/compute/cuda/redist/cuda_cudart/LICENSE.txt \
    -o /usr/local/cuda/EULA.txt
WORKDIR /source
COPY build-native-linux.sh /build-native-linux.sh
ENTRYPOINT ["bash", "/build-native-linux.sh"]
